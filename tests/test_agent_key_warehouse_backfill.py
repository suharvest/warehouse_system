"""Regression: agent api_key 的 NULL warehouse_id 让 MCP 查任何物料都 404。

某客户单仓部署现场：智能体按物料编码查库存，
/api/fuzzy-match 200 且正确解析出 entity_id，紧接着
/api/materials/product-stats?material_id=<id> 却 404 —— 同一个调用者、同一个
物料，两个端点结论相反。用户听到的是"系统中没有与 <SKU> 相似的产品"。

链路：api_keys.warehouse_id IS NULL + role='operate' + user_warehouses 空表
→ build_authorized_scope_predicates() 追加 false()（backend/deps.py:448）
→ product-stats 的 where 恒不命中 → 404。
而 fuzzy-match 不走这个函数，只按 tenant 过滤索引，所以照常返回结果。

为什么迁移 j9k0l1m2n3o4 没兜住：它的 EXISTS(warehouses ...) 前置条件在纯
Alembic 部署路径上执行时必然为假 —— `alembic upgrade head` 跑在
`_seed_base_data()` 之前，那一刻 warehouses 还是空表。迁移匹配 0 行、被记为
已应用、永不重跑；默认仓库随后才种进去，NULL 就永久留在库里。

修复：_backfill_agent_key_warehouse() 在每次启动的 _seed_base_data() 之后重跑
同一份回填（backend/app.py）。
"""
import uuid

import pytest
from sqlalchemy import text


def _sku(prefix="B0201"):
    return f"{prefix}{uuid.uuid4().hex[:6].upper()}"


@pytest.fixture
def engine(test_db, admin_client):
    """admin_client 依赖用于建出 users id=1 / tenant 1（api_keys.user_id 有外键）。"""
    from db import get_engine
    return get_engine()


def _make_ungranted_user(engine):
    """建一个在 user_warehouses 里没有任何授权行的用户。

    现场是 user_warehouses 整张表为空；本地测试库里 user 1 已被其他 fixture
    授过仓，直接复用会让 build_authorized_scope_predicates 走进
    ``warehouse_id.in_([1])`` 而不是 ``false()``，复刻不出那条 404。
    """
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO users (username, password_hash, role, tenant_id) "
                 "VALUES (:u, 'x', 'operate', 1) RETURNING id"),
            {"u": f"agent-{uuid.uuid4().hex[:8]}"},
        ).scalar()


def _make_agent_key(engine, *, role='operate', is_system=1, warehouse_id=None,
                    user_id=1):
    """直插一把 agent key，复刻现场状态（warehouse_id 默认 NULL）。"""
    from database import hash_api_key
    plain = f"wh_{uuid.uuid4().hex}"
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO api_keys (key_hash, name, role, user_id, "
                 "is_system, is_disabled, warehouse_id, tenant_id) "
                 "VALUES (:h, :n, :r, :u, :s, 0, :w, 1)"),
            {"h": hash_api_key(plain), "n": f"Agent: {uuid.uuid4().hex[:4]}",
             "r": role, "u": user_id, "s": is_system, "w": warehouse_id},
        )
    return plain


def _warehouse_id_of(engine, plain):
    from database import hash_api_key
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT warehouse_id FROM api_keys WHERE key_hash = :h"),
            {"h": hash_api_key(plain)},
        ).scalar()


# ---------------------------------------------------------------------------
# 回填本身
# ---------------------------------------------------------------------------

def test_backfill_fills_null_warehouse_on_agent_key(engine):
    from app import _backfill_agent_key_warehouse
    key = _make_agent_key(engine, warehouse_id=None)
    assert _warehouse_id_of(engine, key) is None

    _backfill_agent_key_warehouse()

    assert _warehouse_id_of(engine, key) == 1


def test_backfill_is_idempotent_and_preserves_explicit_binding(engine):
    """已绑仓的 key 不能被改动，重复执行结果不变。"""
    from app import _backfill_agent_key_warehouse
    bound = _make_agent_key(engine, warehouse_id=1)
    null_key = _make_agent_key(engine, warehouse_id=None)

    _backfill_agent_key_warehouse()
    _backfill_agent_key_warehouse()

    assert _warehouse_id_of(engine, bound) == 1
    assert _warehouse_id_of(engine, null_key) == 1


def test_backfill_leaves_admin_key_null(engine):
    """admin key 的 NULL 语义是"全仓可见"，绑单仓反而缩小权限。"""
    from app import _backfill_agent_key_warehouse
    key = _make_agent_key(engine, role='admin', warehouse_id=None)

    _backfill_agent_key_warehouse()

    assert _warehouse_id_of(engine, key) is None


def test_backfill_leaves_non_system_key_null(engine):
    """用户自建的 key 不属于回填范围，保持原样。"""
    from app import _backfill_agent_key_warehouse
    key = _make_agent_key(engine, is_system=0, warehouse_id=None)

    _backfill_agent_key_warehouse()

    assert _warehouse_id_of(engine, key) is None


# ---------------------------------------------------------------------------
# 端到端：复刻"fuzzy 查得到、product-stats 404"的现场
# ---------------------------------------------------------------------------

def test_product_stats_by_sku_404_before_backfill_and_ok_after(
    engine, admin_client, client
):
    from app import _backfill_agent_key_warehouse

    sku = _sku()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO materials (name, sku, category, unit, quantity, "
                 "safe_stock, location, is_disabled, tenant_id, warehouse_id) "
                 "VALUES (:n, :s, '未分类', 'Pcs', 0, 0, 'A-01', 0, 1, 1)"),
            {"n": f"测试物料-{sku}", "s": sku},
        )

    key = _make_agent_key(engine, warehouse_id=None,
                          user_id=_make_ungranted_user(engine))
    headers = {"X-API-Key": key}

    # 现场状态：按 SKU 精确查 → 404「产品不存在」
    before = client.get(f"/api/materials/product-stats?name={sku}",
                            headers=headers)
    assert before.status_code == 404, before.text

    _backfill_agent_key_warehouse()

    after = client.get(f"/api/materials/product-stats?name={sku}",
                           headers=headers)
    assert after.status_code == 200, after.text
    assert after.json()["sku"] == sku


def test_product_stats_by_material_id_matches_fuzzy_match_result(
    engine, admin_client, client
):
    """回填后，fuzzy-match 解析出的 entity_id 必须能被 product-stats 查到。

    这正是现场那条断裂：fuzzy-match 200 → product-stats?material_id=<id> 404。
    """
    from app import _backfill_agent_key_warehouse, get_fuzzy_matcher

    sku = _sku()
    name = f"测试物料B-{sku}"
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO materials (name, sku, category, unit, quantity, "
                 "safe_stock, location, is_disabled, tenant_id, warehouse_id) "
                 "VALUES (:n, :s, '未分类', 'Pcs', 0, 0, 'A-01', 0, 1, 1)"),
            {"n": name, "s": sku},
        )
    # 直插绕过了 API，必须手动失效模糊索引，否则查不到（既有踩坑）。
    get_fuzzy_matcher().invalidate_cache(entity_type='material')

    key = _make_agent_key(engine, warehouse_id=None,
                          user_id=_make_ungranted_user(engine))
    headers = {"X-API-Key": key}
    _backfill_agent_key_warehouse()

    fm = client.get(
        f"/api/fuzzy-match?q={sku}&entity_type=material", headers=headers)
    assert fm.status_code == 200, fm.text
    best = fm.json()["best_match"]
    assert best is not None, fm.text

    stats = client.get(
        f"/api/materials/product-stats?material_id={best['entity_id']}",
        headers=headers)
    assert stats.status_code == 200, stats.text
    assert stats.json()["sku"] == sku

