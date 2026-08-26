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
def _multi_tenant(monkeypatch):
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
