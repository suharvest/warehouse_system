"""
Multi-tenant behavior tests.
"""
import uuid
import importlib
import os
import tempfile
import sqlite3
from io import BytesIO

import pytest


def _is_sqlite_backend() -> bool:
    url = os.environ.get('DATABASE_URL', '')
    return (not url) or url.startswith('sqlite')


sqlite_only = pytest.mark.skipif(
    not _is_sqlite_backend(),
    reason="sqlite-only feature (db export/import/clear streams a literal .db file)",
)


def _as_global_admin(admin_client):
    from database import get_db_connection

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()


def test_global_admin_can_create_warehouse_for_selected_tenant(admin_client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_resp = admin_client.post("/api/tenants", json={
        "slug": f"tenant-{suffix}",
        "name": f"Tenant {suffix}",
    })
    assert tenant_resp.status_code == 200, tenant_resp.text
    tenant_id = tenant_resp.json()["id"]

    wh_resp = admin_client.post("/api/warehouses", json={
        "slug": f"wh-{suffix}",
        "name": f"Warehouse {suffix}",
        "tenant_id": tenant_id,
    })
    assert wh_resp.status_code == 200, wh_resp.text
    assert wh_resp.json()["tenant_id"] == tenant_id


def test_disabled_tenant_user_cannot_login(admin_client, client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_resp = admin_client.post("/api/tenants", json={
        "slug": f"tenant-{suffix}",
        "name": f"Tenant {suffix}",
    })
    assert tenant_resp.status_code == 200, tenant_resp.text
    tenant_id = tenant_resp.json()["id"]

    user_resp = admin_client.post("/api/users", json={
        "username": f"user-{suffix}",
        "password": "Pass123!",
        "display_name": "Tenant User",
        "role": "view",
        "tenant_id": tenant_id,
    })
    assert user_resp.status_code == 200, user_resp.text

    disable_resp = admin_client.delete(f"/api/tenants/{tenant_id}")
    assert disable_resp.status_code == 200, disable_resp.text

    login_resp = client.post("/api/auth/login", json={
        "username": f"user-{suffix}",
        "password": "Pass123!",
    })
    assert login_resp.status_code == 200
    data = login_resp.json()
    assert data["success"] is False
    assert "租户已停用" in data["message"]


def test_duplicate_username_login_is_rejected(admin_client, client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_resp = admin_client.post("/api/tenants", json={
        "slug": f"tenant-login-{suffix}",
        "name": f"Tenant Login {suffix}",
    })
    assert tenant_resp.status_code == 200, tenant_resp.text
    tenant_id = tenant_resp.json()["id"]

    from database import get_db_connection, hash_password

    username = f"seeed-{suffix}"
    password_hash = hash_password("Same123!")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (username, password_hash, role, display_name, tenant_id)
        VALUES (?, ?, 'admin', 'Global Seeed', NULL)
    """, (username, password_hash))
    cursor.execute("""
        INSERT INTO users (username, password_hash, role, display_name, tenant_id)
        VALUES (?, ?, 'admin', 'Tenant Seeed', ?)
    """, (username, password_hash, tenant_id))
    conn.commit()
    conn.close()

    login_resp = client.post("/api/auth/login", json={
        "username": username,
        "password": "Same123!",
    })
    assert login_resp.status_code == 200
    data = login_resp.json()
    assert data["success"] is False
    assert "同名账号存在于多个租户" in data["message"]


def test_global_admin_can_create_another_global_admin(admin_client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    resp = admin_client.post("/api/users", json={
        "username": f"global-admin-{suffix}",
        "password": "Admin123!",
        "display_name": "Global Admin",
        "role": "admin",
        "tenant_id": None,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant_id"] is None


def test_username_is_globally_unique_across_tenants(admin_client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_resp = admin_client.post("/api/tenants", json={
        "slug": f"tenant-unique-{suffix}",
        "name": f"Tenant Unique {suffix}",
    })
    assert tenant_resp.status_code == 200, tenant_resp.text
    tenant_id = tenant_resp.json()["id"]

    username = f"unique-see-{suffix}"
    global_resp = admin_client.post("/api/users", json={
        "username": username,
        "password": "Admin123!",
        "display_name": "Global Admin",
        "role": "admin",
        "tenant_id": None,
    })
    assert global_resp.status_code == 200, global_resp.text

    tenant_resp = admin_client.post("/api/users", json={
        "username": username,
        "password": "Admin123!",
        "display_name": "Tenant Admin",
        "role": "admin",
        "tenant_id": tenant_id,
    })
    assert tenant_resp.status_code == 400
    assert "用户名已存在" in tenant_resp.text


def test_global_non_admin_user_is_rejected(admin_client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    resp = admin_client.post("/api/users", json={
        "username": f"global-view-{suffix}",
        "password": "View123!",
        "display_name": "Global View",
        "role": "view",
        "tenant_id": None,
    })
    assert resp.status_code == 400
    assert "全局用户必须是管理员角色" in resp.text


@sqlite_only
def test_multi_tenant_migration_keeps_one_global_admin(monkeypatch):
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("INIT_MOCK_DATA", "0")

    import database
    old_database_path = getattr(database, "DATABASE_PATH", None)

    try:
        importlib.reload(database)
        database.init_database()

        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (username, password_hash, role, display_name, tenant_id, created_at)
            VALUES ('legacy-admin', ?, 'admin', 'Legacy Admin', 1, '2026-01-01 00:00:00')
        """, (database.hash_password("Admin123!"),))
        conn.commit()
        conn.close()

        database.init_database()

        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT tenant_id FROM users WHERE username = 'legacy-admin'")
        row = cursor.fetchone()
        conn.close()

        assert row["tenant_id"] is None
    finally:
        if old_database_path is not None:
            database.DATABASE_PATH = old_database_path
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _create_tenant_admin_with_warehouse(admin_client, suffix):
    tenant_resp = admin_client.post("/api/tenants", json={
        "slug": f"tenant-db-{suffix}",
        "name": f"Tenant DB {suffix}",
    })
    assert tenant_resp.status_code == 200, tenant_resp.text
    tenant_id = tenant_resp.json()["id"]

    wh_resp = admin_client.post("/api/warehouses", json={
        "slug": f"wh-db-{suffix}",
        "name": f"Warehouse DB {suffix}",
        "tenant_id": tenant_id,
    })
    assert wh_resp.status_code == 200, wh_resp.text
    warehouse_id = wh_resp.json()["id"]

    username = f"tenant-db-admin-{suffix}"
    user_resp = admin_client.post("/api/users", json={
        "username": username,
        "password": "Admin123!",
        "display_name": "Tenant DB Admin",
        "role": "admin",
        "tenant_id": tenant_id,
    })
    assert user_resp.status_code == 200, user_resp.text
    return tenant_id, warehouse_id, username


def _login_as(app_instance, username, password="Admin123!"):
    from fastapi.testclient import TestClient

    c = TestClient(app_instance)
    resp = c.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True, resp.text
    return c


@sqlite_only
def test_tenant_database_export_is_scoped(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_id, warehouse_id, username = _create_tenant_admin_with_warehouse(admin_client, suffix)

    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO materials (name, sku, category, quantity, unit, warehouse_id, tenant_id)
        VALUES (?, ?, 'TenantCat', 7, 'pcs', ?, ?)
    """, (f"Tenant Material {suffix}", f"TENANT-EXP-{suffix}", warehouse_id, tenant_id))
    cursor.execute("""
        INSERT INTO materials (name, sku, category, quantity, unit, warehouse_id, tenant_id)
        VALUES (?, ?, 'DefaultCat', 9, 'pcs', 1, 1)
    """, (f"Default Material {suffix}", f"DEFAULT-EXP-{suffix}"))
    conn.commit()
    conn.close()

    tenant_client = _login_as(app_instance, username)
    resp = tenant_client.get("/api/database/export")
    assert resp.status_code == 200, resp.text

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(resp.content)
        exported = sqlite3.connect(path)
        exported.row_factory = sqlite3.Row
        cur = exported.cursor()
        cur.execute("SELECT DISTINCT tenant_id FROM materials")
        assert [row["tenant_id"] for row in cur.fetchall()] == [tenant_id]
        cur.execute("SELECT sku FROM materials")
        skus = {row["sku"] for row in cur.fetchall()}
        assert f"TENANT-EXP-{suffix}" in skus
        assert f"DEFAULT-EXP-{suffix}" not in skus
        exported.close()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@sqlite_only
def test_tenant_database_clear_only_clears_current_tenant(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_id, warehouse_id, username = _create_tenant_admin_with_warehouse(admin_client, suffix)

    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO materials (name, sku, category, quantity, unit, warehouse_id, tenant_id)
        VALUES (?, ?, 'TenantCat', 7, 'pcs', ?, ?)
    """, (f"Tenant Clear {suffix}", f"TENANT-CLR-{suffix}", warehouse_id, tenant_id))
    cursor.execute("""
        INSERT INTO materials (name, sku, category, quantity, unit, warehouse_id, tenant_id)
        VALUES (?, ?, 'DefaultCat', 9, 'pcs', 1, 1)
    """, (f"Default Clear {suffix}", f"DEFAULT-CLR-{suffix}"))
    conn.commit()
    conn.close()

    tenant_client = _login_as(app_instance, username)
    resp = tenant_client.post("/api/database/clear", json={"confirm": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM materials WHERE tenant_id = ?", (tenant_id,))
    assert cursor.fetchone()["c"] == 0
    cursor.execute("SELECT COUNT(*) as c FROM materials WHERE sku = ?", (f"DEFAULT-CLR-{suffix}",))
    assert cursor.fetchone()["c"] == 1
    cursor.execute("SELECT COUNT(*) as c FROM warehouses WHERE tenant_id = ?", (tenant_id,))
    assert cursor.fetchone()["c"] == 1
    conn.close()


@sqlite_only
def test_tenant_database_import_forces_current_tenant(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_id, _, username = _create_tenant_admin_with_warehouse(admin_client, suffix)

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        source = sqlite3.connect(path)
        source.execute("""
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY,
                name TEXT,
                sku TEXT,
                category TEXT,
                quantity INTEGER,
                unit TEXT,
                warehouse_id INTEGER,
                tenant_id INTEGER
            )
        """)
        source.execute("""
            INSERT INTO materials (id, name, sku, category, quantity, unit, warehouse_id, tenant_id)
            VALUES (99, ?, ?, 'Imported', 12, 'pcs', 999, 999)
        """, (f"Imported {suffix}", f"TENANT-IMP-{suffix}"))
        source.commit()
        source.close()

        with open(path, "rb") as f:
            content = f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    tenant_client = _login_as(app_instance, username)
    resp = tenant_client.post(
        "/api/database/import",
        files={"file": ("tenant-import.db", BytesIO(content), "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tenant_id, warehouse_id FROM materials WHERE sku = ?", (f"TENANT-IMP-{suffix}",))
    row = cursor.fetchone()
    assert row["tenant_id"] == tenant_id
    cursor.execute("SELECT tenant_id FROM warehouses WHERE id = ?", (row["warehouse_id"],))
    assert cursor.fetchone()["tenant_id"] == tenant_id
    conn.close()


def test_tenant_admin_boundaries_for_users_and_warehouses(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_a, warehouse_a, admin_a = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-a")
    tenant_b, warehouse_b, admin_b = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-b")
    client_a = _login_as(app_instance, admin_a)
    client_b = _login_as(app_instance, admin_b)

    resp = client_a.post("/api/warehouses", json={
        "slug": f"cross-wh-{suffix}",
        "name": "Cross Warehouse",
        "tenant_id": tenant_b,
    })
    assert resp.status_code == 403

    resp = client_a.post("/api/users", json={
        "username": f"cross-user-{suffix}",
        "password": "Admin123!",
        "display_name": "Cross User",
        "role": "view",
        "tenant_id": tenant_b,
    })
    assert resp.status_code == 403

    user_a_resp = client_a.post("/api/users", json={
        "username": f"tenant-a-user-{suffix}",
        "password": "Admin123!",
        "display_name": "Tenant A User",
        "role": "view",
    })
    assert user_a_resp.status_code == 200, user_a_resp.text
    user_a_id = user_a_resp.json()["id"]

    resp = client_a.put(f"/api/users/{user_a_id}/warehouses", json={"warehouse_ids": [warehouse_b]})
    assert resp.status_code == 400

    users_b = client_b.get("/api/users")
    assert users_b.status_code == 200, users_b.text
    listed_tenants = {row["tenant_id"] for row in users_b.json()}
    assert listed_tenants == {tenant_b}
    assert all(row["username"] != f"tenant-a-user-{suffix}" for row in users_b.json())

    own_assign = client_a.put(f"/api/users/{user_a_id}/warehouses", json={"warehouse_ids": [warehouse_a]})
    assert own_assign.status_code == 200, own_assign.text


def test_tenant_admin_cannot_cross_tenant_api_keys_or_mcp(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    tenant_a, warehouse_a, admin_a = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-a")
    tenant_b, warehouse_b, admin_b = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-b")
    client_a = _login_as(app_instance, admin_a)
    client_b = _login_as(app_instance, admin_b)

    cross_key = client_a.post("/api/api-keys", json={
        "name": f"cross-key-{suffix}",
        "role": "operate",
        "warehouse_id": warehouse_b,
    })
    assert cross_key.status_code == 403

    own_key = client_a.post("/api/api-keys", json={
        "name": f"tenant-a-key-{suffix}",
        "role": "operate",
        "warehouse_id": warehouse_a,
    })
    assert own_key.status_code == 200, own_key.text
    key_id = own_key.json()["id"]

    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tenant_id, warehouse_id FROM api_keys WHERE id = ?", (key_id,))
    row = cursor.fetchone()
    assert row["tenant_id"] == tenant_a
    assert row["warehouse_id"] == warehouse_a
    conn.close()

    keys_b = client_b.get("/api/api-keys")
    assert keys_b.status_code == 200, keys_b.text
    assert all(row["id"] != key_id for row in keys_b.json())
    assert client_b.delete(f"/api/api-keys/{key_id}").status_code == 403
    assert client_b.put(f"/api/api-keys/{key_id}/status", json={"disabled": True}).status_code == 403

    cross_mcp = client_a.post("/api/mcp/connections", json={
        "name": f"cross-mcp-{suffix}",
        "mcp_endpoint": f"http://127.0.0.1:9/mcp/{suffix}/cross",
        "role": "operate",
        "auto_start": False,
        "warehouse_id": warehouse_b,
    })
    assert cross_mcp.status_code == 403

    own_mcp = client_a.post("/api/mcp/connections", json={
        "name": f"tenant-a-mcp-{suffix}",
        "mcp_endpoint": f"http://127.0.0.1:9/mcp/{suffix}/own",
        "role": "operate",
        "auto_start": False,
        "warehouse_id": warehouse_a,
    })
    assert own_mcp.status_code == 200, own_mcp.text
    conn_id = own_mcp.json()["connection"]["id"]

    mcp_b = client_b.get("/api/mcp/connections")
    assert mcp_b.status_code == 200, mcp_b.text
    assert all(row["id"] != conn_id for row in mcp_b.json())
    assert client_b.put(f"/api/mcp/connections/{conn_id}", json={"name": "blocked"}).status_code == 403


def test_tenant_contacts_are_scoped_for_read_update_delete(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    _tenant_a, _warehouse_a, admin_a = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-a")
    _tenant_b, _warehouse_b, admin_b = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-b")
    client_a = _login_as(app_instance, admin_a)
    client_b = _login_as(app_instance, admin_b)

    create_resp = client_a.post("/api/contacts", json={
        "name": f"Tenant A Supplier {suffix}",
        "is_supplier": True,
        "is_customer": False,
    })
    assert create_resp.status_code == 200, create_resp.text
    contact_id = create_resp.json()["id"]

    list_b = client_b.get("/api/contacts", params={"page_size": 20})
    assert list_b.status_code == 200, list_b.text
    assert all(row["id"] != contact_id for row in list_b.json()["items"])

    assert client_b.get(f"/api/contacts/{contact_id}").status_code == 403
    assert client_b.put(f"/api/contacts/{contact_id}", json={"name": "blocked"}).status_code == 403
    assert client_b.delete(f"/api/contacts/{contact_id}").status_code == 403


def test_tenant_stock_write_to_other_tenant_warehouse_is_forbidden(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    _tenant_a, _warehouse_a, admin_a = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-a")
    tenant_b, warehouse_b, _admin_b = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-b")

    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO materials (name, sku, category, quantity, unit, warehouse_id, tenant_id)
        VALUES (?, ?, 'Boundary', 1, 'pcs', ?, ?)
    """, (f"Tenant B Material {suffix}", f"TENANT-B-STOCK-{suffix}", warehouse_b, tenant_b))
    conn.commit()
    conn.close()

    client_a = _login_as(app_instance, admin_a)
    resp = client_a.post("/api/materials/stock-in", json={
        "product_name": f"Tenant B Material {suffix}",
        "quantity": 1,
        "reason_category": "purchase",
        "warehouse_id": warehouse_b,
        "fuzzy": False,
    })
    assert resp.status_code == 403
