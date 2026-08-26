"""``POST /api/inventory/reset`` —— 按租户/仓库清空库存数据。

这个接口是为了替代 ``/api/database/clear`` 在"导错单子想重导"场景下的使用：
clear 是 sqlite-only（MySQL 部署直接 400），而且会删掉 warehouses 并把
api_keys / mcp_connections 的 warehouse_id 置 NULL —— 仓库换新 id、智能体
key 失去仓库绑定后查不到任何物料。

所以这里的断言分两半：**该删的删干净**（materials / batches /
inventory_records / batch_consumptions），**不该动的一个都没动**
（warehouses / contacts / api_keys.warehouse_id / mcp_connections.warehouse_id）。

本文件**不加** ``sqlite_only`` 标记：这个接口全程走 SQLAlchemy Core，
MySQL 正是它的目标部署，必须跟着 MySQL 兼容性任务一起跑。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, func, and_


def _seed(suffix, warehouse_count=1):
    """用 SA Core 造一个独立租户（含仓库/管理员/物料/批次/记录/消耗/联系方/密钥/MCP）。

    走 Core 而不是 sqlite3，是为了这份夹具在 MySQL 上同样可用。
    """
    from db import get_engine
    from database import hash_password
    from metadata import (
        tenants, warehouses, users, materials, batches, inventory_records,
        batch_consumptions, contacts, api_keys, mcp_connections,
    )

    eng = get_engine()
    seeded = {"warehouses": [], "materials": {}}
    username = f"resetadmin-{suffix}"

    with eng.begin() as conn:
        tenant_id = conn.execute(insert(tenants).values(
            slug=f"reset-{suffix}", name=f"Reset {suffix}",
        )).inserted_primary_key[0]

        for i in range(warehouse_count):
            wh_id = conn.execute(insert(warehouses).values(
                slug=f"reset-wh-{suffix}-{i}", name=f"Reset WH {i}", tenant_id=tenant_id,
            )).inserted_primary_key[0]
            seeded["warehouses"].append(wh_id)

        user_id = conn.execute(insert(users).values(
            username=username, password_hash=hash_password("Admin123!"),
            role="admin", display_name="Reset Admin", tenant_id=tenant_id,
        )).inserted_primary_key[0]

        contact_id = conn.execute(insert(contacts).values(
            name=f"供应商-{suffix}", is_supplier=1, tenant_id=tenant_id,
        )).inserted_primary_key[0]

        for wh_id in seeded["warehouses"]:
            material_id = conn.execute(insert(materials).values(
                name=f"物料-{suffix}-{wh_id}", sku=f"RST-{suffix}-{wh_id}",
                category="测试", quantity=10, unit="个",
                warehouse_id=wh_id, tenant_id=tenant_id,
            )).inserted_primary_key[0]
            batch_id = conn.execute(insert(batches).values(
                batch_no=f"B-{suffix}-{wh_id}", material_id=material_id,
                quantity=10, initial_quantity=10, contact_id=contact_id,
                warehouse_id=wh_id, tenant_id=tenant_id,
            )).inserted_primary_key[0]
            record_id = conn.execute(insert(inventory_records).values(
                material_id=material_id, type="in", quantity=10, operator="test",
                batch_id=batch_id, warehouse_id=wh_id, tenant_id=tenant_id,
            )).inserted_primary_key[0]
            conn.execute(insert(batch_consumptions).values(
                record_id=record_id, batch_id=batch_id, quantity=10,
                warehouse_id=wh_id, tenant_id=tenant_id,
            ))
            seeded["materials"][wh_id] = material_id

        bound_wh = seeded["warehouses"][0]
        conn.execute(insert(api_keys).values(
            key_hash=f"hash-{suffix}", name=f"key-{suffix}", role="operate",
            user_id=user_id, warehouse_id=bound_wh, tenant_id=tenant_id,
        ))
        conn.execute(insert(mcp_connections).values(
            id=f"mcp-{suffix}", name=f"mcp-{suffix}", mcp_endpoint="wss://example/mcp",
            api_key="k", role="operate", warehouse_id=bound_wh, tenant_id=tenant_id,
        ))

    seeded.update(tenant_id=tenant_id, username=username,
                  contact_id=contact_id, bound_warehouse_id=bound_wh)
    return seeded


def _count(table, **filters):
    from db import get_engine
    preds = [getattr(table.c, k) == v for k, v in filters.items()]
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(table).where(and_(*preds))
        ).scalar_one()


def _login(app_instance, username):
    c = TestClient(app_instance)
    resp = c.post("/api/auth/login", json={"username": username, "password": "Admin123!"})
    assert resp.status_code == 200, resp.text
    return c


@pytest.fixture(autouse=True)
def _multi_tenant(monkeypatch, admin_client):
    """admin_client 不是给用例用的，是为了强制 session 级 _admin_setup 先跑完。

    _seed() 直接往 users 插行，一旦它先于 /api/auth/setup 执行，setup 就会撞上
    "系统已初始化，无法重复设置"，让同一批次里所有依赖 admin_client 的用例集体
    ERROR（全量跑因为字母序在前的文件已建好 admin 而看不到，单独挑几个文件跑才炸）。
    """
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")


def test_reset_clears_inventory_and_keeps_bindings(app_instance):
    from metadata import (
        materials, batches, inventory_records, batch_consumptions,
        warehouses, contacts, api_keys, mcp_connections,
    )
    suffix = uuid.uuid4().hex[:8]
    seeded = _seed(suffix)
    tid = seeded["tenant_id"]
    client = _login(app_instance, seeded["username"])

    resp = client.post("/api/inventory/reset", json={"confirm": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["details"] == {
        "batch_consumptions": 1, "inventory_records": 1, "batches": 1, "materials": 1,
    }

    # 该删的：四张业务表清零
    for table in (materials, batches, inventory_records, batch_consumptions):
        assert _count(table, tenant_id=tid) == 0, f"{table.name} 未清空"

    # 不该动的：仓库还在、id 没变，联系方还在，密钥/MCP 的仓库绑定原样保留
    assert _count(warehouses, tenant_id=tid) == len(seeded["warehouses"])
    assert _count(contacts, tenant_id=tid) == 1
    assert _count(api_keys, tenant_id=tid, warehouse_id=seeded["bound_warehouse_id"]) == 1
    assert _count(mcp_connections, tenant_id=tid, warehouse_id=seeded["bound_warehouse_id"]) == 1


def test_reset_scoped_to_one_warehouse(app_instance):
    from metadata import materials, batches, inventory_records, batch_consumptions
    suffix = uuid.uuid4().hex[:8]
    seeded = _seed(suffix, warehouse_count=2)
    wh_clear, wh_keep = seeded["warehouses"]
    client = _login(app_instance, seeded["username"])

    resp = client.post("/api/inventory/reset", json={"confirm": True, "warehouse_id": wh_clear})
    assert resp.status_code == 200, resp.text

    for table in (materials, batches, inventory_records, batch_consumptions):
        assert _count(table, warehouse_id=wh_clear) == 0, f"{table.name} 未清空"
        assert _count(table, warehouse_id=wh_keep) == 1, f"{table.name} 的另一个仓库被误删"


def test_reset_does_not_touch_other_tenants(app_instance):
    from metadata import materials, batches, inventory_records, batch_consumptions
    mine = _seed(uuid.uuid4().hex[:8])
    other = _seed(uuid.uuid4().hex[:8])
    client = _login(app_instance, mine["username"])

    assert client.post("/api/inventory/reset", json={"confirm": True}).status_code == 200

    for table in (materials, batches, inventory_records, batch_consumptions):
        assert _count(table, tenant_id=other["tenant_id"]) == 1, f"{table.name} 跨租户被删"


def test_reset_rejects_other_tenants_warehouse(app_instance):
    from metadata import materials
    mine = _seed(uuid.uuid4().hex[:8])
    other = _seed(uuid.uuid4().hex[:8])
    client = _login(app_instance, mine["username"])

    resp = client.post("/api/inventory/reset", json={
        "confirm": True, "warehouse_id": other["warehouses"][0],
    })
    assert resp.status_code == 403, resp.text
    assert _count(materials, tenant_id=other["tenant_id"]) == 1
    assert _count(materials, tenant_id=mine["tenant_id"]) == 1


def test_reset_requires_confirm(app_instance):
    from metadata import materials
    seeded = _seed(uuid.uuid4().hex[:8])
    client = _login(app_instance, seeded["username"])

    resp = client.post("/api/inventory/reset", json={"confirm": False})
    assert resp.status_code == 400, resp.text
    assert _count(materials, tenant_id=seeded["tenant_id"]) == 1


def test_reset_requires_admin(app_instance):
    """operate 角色不足以清空——这是不可撤销操作，权限门槛与 database/clear 一致。"""
    from db import get_engine
    from database import hash_password
    from metadata import users, materials
    from sqlalchemy import update

    seeded = _seed(uuid.uuid4().hex[:8])
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.username == seeded["username"])
                     .values(role="operate", password_hash=hash_password("Admin123!")))
    client = _login(app_instance, seeded["username"])

    resp = client.post("/api/inventory/reset", json={"confirm": True})
    assert resp.status_code == 403, resp.text
    assert _count(materials, tenant_id=seeded["tenant_id"]) == 1


def test_reset_invalidates_fuzzy_index(app_instance):
    """清空后模糊索引不能再吐出已删物料——否则语音查询拿到不存在的 id。"""
    seeded = _seed(uuid.uuid4().hex[:8])
    client = _login(app_instance, seeded["username"])
    sku = f"RST-{seeded['username'].split('-')[1]}-{seeded['warehouses'][0]}"

    def _skus():
        r = client.get("/api/fuzzy-match", params={
            "q": sku, "entity_type": "material", "threshold": 50,
        })
        assert r.status_code == 200, r.text
        return {c.get("extra", {}).get("sku") for c in r.json()["candidates"]}

    assert sku in _skus(), "precondition: 物料已进索引"
    assert client.post("/api/inventory/reset", json={"confirm": True}).status_code == 200
    assert sku not in _skus(), "索引未失效，已删物料仍可被匹配"


def _seed_global_admin(suffix):
    """建一个 tenant_id 为 NULL 的全局管理员（多租户部署里的运维账号）。"""
    from db import get_engine
    from database import hash_password
    from metadata import users

    username = f"globaladmin-{suffix}"
    with get_engine().begin() as conn:
        conn.execute(insert(users).values(
            username=username, password_hash=hash_password("Admin123!"),
            role="admin", display_name="Global Admin", tenant_id=None,
        ))
    return username


def test_global_admin_must_name_target_tenant(app_instance):
    """全局管理员没有"自己的租户"，不显式指定就无从判断该删谁的数据 —— 必须 400。"""
    from metadata import materials
    seeded = _seed(uuid.uuid4().hex[:8])
    client = _login(app_instance, _seed_global_admin(uuid.uuid4().hex[:8]))

    resp = client.post("/api/inventory/reset", json={"confirm": True})
    assert resp.status_code == 400, resp.text
    assert _count(materials, tenant_id=seeded["tenant_id"]) == 1


def test_global_admin_clears_only_named_tenant(app_instance):
    from metadata import materials
    target = _seed(uuid.uuid4().hex[:8])
    bystander = _seed(uuid.uuid4().hex[:8])
    client = _login(app_instance, _seed_global_admin(uuid.uuid4().hex[:8]))

    resp = client.post("/api/inventory/reset", json={
        "confirm": True, "target_tenant_id": target["tenant_id"],
    })
    assert resp.status_code == 200, resp.text
    assert _count(materials, tenant_id=target["tenant_id"]) == 0
    assert _count(materials, tenant_id=bystander["tenant_id"]) == 1


def test_global_admin_rejects_warehouse_of_another_tenant(app_instance):
    """target_tenant_id 与 warehouse_id 搭配错了必须拒绝，否则会删到别的租户。"""
    from metadata import materials
    target = _seed(uuid.uuid4().hex[:8])
    other = _seed(uuid.uuid4().hex[:8])
    client = _login(app_instance, _seed_global_admin(uuid.uuid4().hex[:8]))

    resp = client.post("/api/inventory/reset", json={
        "confirm": True,
        "target_tenant_id": target["tenant_id"],
        "warehouse_id": other["warehouses"][0],
    })
    assert resp.status_code == 400, resp.text
    for seeded in (target, other):
        assert _count(materials, tenant_id=seeded["tenant_id"]) == 1


def test_reset_deletes_legacy_consumption_rows_without_scope_columns(app_instance):
    """老库里的 batch_consumptions 可能 tenant_id/warehouse_id 为空。

    这类行只按自身作用域删会成为孤儿（父记录已删、它还在），所以删除条件里带了
    按父记录/父批次的分支。夹具三列一致时验证不到这一支，这里显式把它们置空。
    """
    from db import get_engine
    from sqlalchemy import update
    from metadata import batch_consumptions

    seeded = _seed(uuid.uuid4().hex[:8])
    with get_engine().begin() as conn:
        conn.execute(update(batch_consumptions)
                     .where(batch_consumptions.c.tenant_id == seeded["tenant_id"])
                     .values(tenant_id=None, warehouse_id=None))
    client = _login(app_instance, seeded["username"])

    resp = client.post("/api/inventory/reset", json={"confirm": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["details"]["batch_consumptions"] == 1
    with get_engine().connect() as conn:
        left = conn.execute(
            select(func.count()).select_from(batch_consumptions)
            .where(batch_consumptions.c.tenant_id.is_(None))
        ).scalar_one()
    assert left == 0, "作用域列为空的历史消耗行没被删掉"


def test_reset_deletes_disabled_materials(app_instance):
    """reset 不区分启用/禁用 —— 这也是"先导出再清空"必须显式带上 disabled 状态
    才算完整备份的原因（见 database.js 的导出参数）。"""
    from db import get_engine
    from sqlalchemy import update
    from metadata import materials

    seeded = _seed(uuid.uuid4().hex[:8])
    with get_engine().begin() as conn:
        conn.execute(update(materials)
                     .where(materials.c.tenant_id == seeded["tenant_id"])
                     .values(is_disabled=1))
    client = _login(app_instance, seeded["username"])

    assert client.post("/api/inventory/reset", json={"confirm": True}).status_code == 200
    assert _count(materials, tenant_id=seeded["tenant_id"]) == 0
