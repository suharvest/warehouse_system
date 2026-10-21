"""``POST /api/database/import`` 不得给租户留下重复的默认仓库（issue #24）。

整库导入 = 先清空该租户的仓库业务数据，再把备份插回去。清空那一步过去会**无条件**
补建一个默认仓库占位；备份里那行 slug 撞名后被 ``_unique_warehouse_slug`` 改名，于是
导入结束租户手里有两个仓库、两个都 ``is_default=1``。

后果不只是列表里多一行：仓库数 > 1 之后 ``infer_single_writable_warehouse_id()``
不再自动选仓，任何没显式带 ``warehouse_id`` 的写操作（包括「库存列表 → 导入」）都会
400「写操作必须指定仓库 (warehouse_id)」—— 现场报障「清除所有数据，重新导入，报错」
就是这个。

隔离：与 ``test_tenants`` / ``test_import_fuzzy_invalidation`` 同款做法，跑在
``multi_tenant`` 下、只碰一次性的租户，不动默认租户 1。
"""
import os
import sqlite3
import tempfile
import uuid
from io import BytesIO

from test_tenants import (
    sqlite_only,
    _as_global_admin,
    _create_tenant_admin_with_warehouse,
    _login_as,
)


def _sqlite_bytes(build) -> bytes:
    """把 ``build(conn)`` 写出的表/行落成一个临时 .db，返回文件内容。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        build(conn)
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _materials_table(conn, sku):
    conn.execute(
        "CREATE TABLE materials (id INTEGER PRIMARY KEY, name TEXT, sku TEXT, "
        "category TEXT, quantity INTEGER, unit TEXT, warehouse_id INTEGER, tenant_id INTEGER)"
    )
    conn.execute(
        "INSERT INTO materials (id, name, sku, category, quantity, unit, warehouse_id, tenant_id) "
        "VALUES (1, ?, ?, 'Imported', 5, '个', 1, 1)",
        (f"物料-{sku}", sku),
    )


def _warehouses_table(conn, rows):
    conn.execute(
        "CREATE TABLE warehouses (id INTEGER PRIMARY KEY, slug TEXT, name TEXT, "
        "is_default INTEGER, is_disabled INTEGER, tenant_id INTEGER, created_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO warehouses (id, slug, name, is_default, is_disabled) "
        "VALUES (?, ?, ?, ?, 0)",
        rows,
    )


def _tenant_warehouses(tenant_id):
    from sqlalchemy import select

    from db import get_engine
    from metadata import warehouses

    with get_engine().connect() as conn:
        return conn.execute(
            select(warehouses.c.id, warehouses.c.slug, warehouses.c.is_default)
            .where(warehouses.c.tenant_id == tenant_id)
            .order_by(warehouses.c.id)
        ).fetchall()


def _tenant_material_warehouse(tenant_id, sku):
    from sqlalchemy import select

    from db import get_engine
    from metadata import materials, warehouses

    with get_engine().connect() as conn:
        return conn.execute(
            select(materials.c.warehouse_id, warehouses.c.id, warehouses.c.is_default)
            .select_from(materials.outerjoin(warehouses, warehouses.c.id == materials.c.warehouse_id))
            .where(materials.c.tenant_id == tenant_id, materials.c.sku == sku)
        ).first()


def _import_tenant_db(client, payload):
    return client.post(
        "/api/database/import",
        files={"file": ("backup.db", BytesIO(payload), "application/octet-stream")},
    )


def _setup_tenant(app_instance, admin_client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)
    suffix = uuid.uuid4().hex[:8]
    tenant_id, _warehouse_id, username = _create_tenant_admin_with_warehouse(admin_client, suffix)
    return tenant_id, username, suffix, _login_as(app_instance, username)


@sqlite_only
def test_import_does_not_add_a_duplicate_warehouse(app_instance, admin_client, monkeypatch):
    """备份里只有一个仓库时，导入后租户也只该有一个（修复前是两个）。"""
    tenant_id, username, suffix, client = _setup_tenant(app_instance, admin_client, monkeypatch)
    assert len(_tenant_warehouses(tenant_id)) == 1, "前提：租户本来只有 admin 建的一个仓库"

    sku = f"DBIMP-{suffix}"
    payload = _sqlite_bytes(lambda c: (
        _warehouses_table(c, [(1, "default", "默认仓库", 1)]),
        _materials_table(c, sku),
    ))

    resp = _import_tenant_db(client, payload)
    assert resp.status_code == 200, resp.text

    after = _tenant_warehouses(tenant_id)
    assert len(after) == 1, (
        "整库导入后多出了仓库（占位仓库 + 导入仓库）："
        f"{[(r.id, r.slug) for r in after]}"
    )
    assert after[0].slug == "default"
    assert after[0].is_default == 1

    # 物料必须落在那唯一的仓库里，不能挂到一个被删掉的 id 上
    material = _tenant_material_warehouse(tenant_id, sku)
    assert material is not None
    assert material.warehouse_id == after[0].id


@sqlite_only
def test_import_normalizes_is_default_to_exactly_one(app_instance, admin_client, monkeypatch):
    """备份里多行都标了 is_default 时，导入后只保留一行默认仓库。"""
    tenant_id, username, suffix, client = _setup_tenant(app_instance, admin_client, monkeypatch)

    sku = f"DBIMP2-{suffix}"
    payload = _sqlite_bytes(lambda c: (
        _warehouses_table(c, [
            (1, "default", "默认仓库", 1),
            (2, "wh-second", "二号仓", 1),
        ]),
        _materials_table(c, sku),
    ))

    resp = _import_tenant_db(client, payload)
    assert resp.status_code == 200, resp.text

    after = _tenant_warehouses(tenant_id)
    assert len(after) == 2, f"备份里的两个仓库都该保留：{[(r.id, r.slug) for r in after]}"
    assert sum(1 for r in after if r.is_default) == 1, (
        f"默认仓库必须恰好一行，实际 {[(r.slug, r.is_default) for r in after]}"
    )
    assert after[0].slug == "default" and after[0].is_default == 1


@sqlite_only
def test_import_without_warehouses_table_still_creates_a_default(
    app_instance, admin_client, monkeypatch
):
    """精简备份（只有 materials 表）仍要兜底建一个默认仓库，物料挂上去。"""
    tenant_id, username, suffix, client = _setup_tenant(app_instance, admin_client, monkeypatch)

    sku = f"DBIMP3-{suffix}"
    payload = _sqlite_bytes(lambda c: _materials_table(c, sku))

    resp = _import_tenant_db(client, payload)
    assert resp.status_code == 200, resp.text

    after = _tenant_warehouses(tenant_id)
    assert len(after) == 1, f"应只兜底建一个默认仓库：{[(r.id, r.slug) for r in after]}"
    assert after[0].is_default == 1
    material = _tenant_material_warehouse(tenant_id, sku)
    assert material is not None and material.warehouse_id == after[0].id


@sqlite_only
def test_excel_import_after_database_import_does_not_400(
    app_instance, admin_client, monkeypatch
):
    """报障原文的回归：整库导入之后接着导库存，不能再报 400 写操作必须指定仓库。

    UI 在「全部仓库」视图下发的 ``warehouse_id`` 就是 None；修复前租户因重复仓库变成
    多仓库，``infer_single_writable_warehouse_id()`` 失效，这一步直接 400。
    """
    tenant_id, username, suffix, client = _setup_tenant(app_instance, admin_client, monkeypatch)

    payload = _sqlite_bytes(lambda c: (
        _warehouses_table(c, [(1, "default", "默认仓库", 1)]),
        _materials_table(c, f"DBIMP4-{suffix}"),
    ))
    assert _import_tenant_db(client, payload).status_code == 200

    sample = client.get("/api/materials/import-excel/sample")
    assert sample.status_code == 200, sample.text

    preview = client.post(
        "/api/materials/import-excel/preview",
        files={"file": ("inventory.xlsx", BytesIO(sample.content), "application/octet-stream")},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["success"] is True, body

    confirm = client.post("/api/materials/import-excel/confirm", json={
        "changes": body["preview"],
        "confirm_new_skus": True,
        "is_batch_mode": body["is_batch_mode"],
        "warehouse_id": None,
    })
    assert confirm.status_code == 200, (
        f"整库导入后 Excel 导入又 400 了：{confirm.text}"
    )
    assert confirm.json()["success"] is True, confirm.text