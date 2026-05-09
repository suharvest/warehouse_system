"""
R1 regression net: parametrized 404 / cross-tenant 403 for resource families
that go through ``load_or_404`` (warehouses, users, API keys, contacts, MCP,
ERP, face).

These tests are a *baseline* of current behavior. They must pass against the
un-refactored code AND continue to pass after the helper migration.

Error response shape per ``backend/app.py`` exception handler is
``{"error": detail}`` for every HTTPException.
"""
from __future__ import annotations

import os
import uuid

import pytest


def _is_sqlite_backend() -> bool:
    url = os.environ.get('DATABASE_URL', '')
    return (not url) or url.startswith('sqlite')


def _as_global_admin(admin_client):
    """Promote the seeded admin user to global admin (tenant_id=NULL)."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()


def _restore_admin_tenant(admin_client):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = 1 WHERE username = 'admin'")
    conn.commit()
    conn.close()


def _create_tenant_admin(admin_client, suffix):
    """Create tenant + warehouse + tenant-scoped admin user. Returns
    ``(tenant_id, warehouse_id, username, password)``.
    """
    tenant_resp = admin_client.post("/api/tenants", json={
        "slug": f"r1-tenant-{suffix}",
        "name": f"R1 Tenant {suffix}",
    })
    assert tenant_resp.status_code == 200, tenant_resp.text
    tenant_id = tenant_resp.json()["id"]

    wh_resp = admin_client.post("/api/warehouses", json={
        "slug": f"r1-wh-{suffix}",
        "name": f"R1 Warehouse {suffix}",
        "tenant_id": tenant_id,
    })
    assert wh_resp.status_code == 200, wh_resp.text
    warehouse_id = wh_resp.json()["id"]

    username = f"r1-admin-{suffix}"
    password = "Admin123!"
    user_resp = admin_client.post("/api/users", json={
        "username": username,
        "password": password,
        "display_name": "R1 Admin",
        "role": "admin",
        "tenant_id": tenant_id,
    })
    assert user_resp.status_code == 200, user_resp.text
    return tenant_id, warehouse_id, username, password


def _login(app_instance, username, password="Admin123!"):
    from fastapi.testclient import TestClient
    c = TestClient(app_instance)
    resp = c.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True, resp.text
    return c


@pytest.fixture()
def two_tenants(admin_client, app_instance, monkeypatch):
    """Yield two scoped admins each for a separate tenant.

    Returns dict with keys ``a`` / ``b``, each containing
    ``tenant_id``, ``warehouse_id``, ``client``.
    """
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    t_a, wh_a, user_a, pw_a = _create_tenant_admin(admin_client, f"{suffix}-a")
    t_b, wh_b, user_b, pw_b = _create_tenant_admin(admin_client, f"{suffix}-b")

    client_a = _login(app_instance, user_a, pw_a)
    client_b = _login(app_instance, user_b, pw_b)

    yield {
        "global_admin": admin_client,
        "suffix": suffix,
        "a": {"tenant_id": t_a, "warehouse_id": wh_a, "client": client_a, "username": user_a},
        "b": {"tenant_id": t_b, "warehouse_id": wh_b, "client": client_b, "username": user_b},
    }
    # Restore admin tenant scope so unrelated tests don't break.
    try:
        _restore_admin_tenant(admin_client)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers to seed cross-tenant resources of each family.
# Each helper returns a row id that belongs to tenant B.
# ---------------------------------------------------------------------------

def _seed_warehouse_b(env):
    return env["b"]["warehouse_id"]


def _seed_user_b(env):
    """Pick the tenant-B admin user id."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = ?", (env["b"]["username"],))
    row = cur.fetchone()
    conn.close()
    return row["id"]


def _seed_api_key_b(env):
    resp = env["b"]["client"].post("/api/api-keys", json={
        "name": f"r1-key-b-{env['suffix']}",
        "role": "operate",
        "warehouse_id": env["b"]["warehouse_id"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _seed_contact_b(env):
    resp = env["b"]["client"].post("/api/contacts", json={
        "name": f"R1 Contact B {env['suffix']}",
        "is_supplier": True,
        "is_customer": False,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _seed_mcp_b(env):
    resp = env["b"]["client"].post("/api/mcp/connections", json={
        "name": f"r1-mcp-b-{env['suffix']}",
        "mcp_endpoint": "http://127.0.0.1:9/mcp",
        "role": "operate",
        "auto_start": False,
        "warehouse_id": env["b"]["warehouse_id"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["connection"]["id"]


def _seed_face_subject_b(env):
    """Seed a face subject for tenant B by direct DB insert."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO face_subjects (tenant_id, name, is_active, created_at, updated_at) "
        "VALUES (?, ?, 1, datetime('now'), datetime('now'))",
        (env["b"]["tenant_id"], f"R1 Subject B {env['suffix']}"),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


# A nonexistent id used for 404 checks. Pick something well out of range.
NONEXISTENT_ID = 999_999_999


# ---------------------------------------------------------------------------
# Warehouses
# ---------------------------------------------------------------------------

def test_warehouse_put_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/warehouses/{NONEXISTENT_ID}", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json() == {"error": "仓库不存在"}


def test_warehouse_put_403_cross_tenant(two_tenants):
    other = _seed_warehouse_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/warehouses/{other}", json={"name": "x"})
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权操作该仓库"}


def test_warehouse_delete_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/warehouses/{NONEXISTENT_ID}")
    assert resp.status_code == 404
    assert resp.json() == {"error": "仓库不存在"}


def test_warehouse_delete_403_cross_tenant(two_tenants):
    other = _seed_warehouse_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/warehouses/{other}")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权操作该仓库"}


# ---------------------------------------------------------------------------
# Users (warehouse assignment endpoints + user PUT/DELETE)
# ---------------------------------------------------------------------------

def test_user_warehouses_get_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.get(f"/api/users/{NONEXISTENT_ID}/warehouses")
    assert resp.status_code == 404
    assert resp.json() == {"error": "用户不存在"}


def test_user_warehouses_get_403_cross_tenant(two_tenants):
    other = _seed_user_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.get(f"/api/users/{other}/warehouses")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权访问其他租户的用户"}


def test_user_warehouses_put_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/users/{NONEXISTENT_ID}/warehouses", json={"warehouse_ids": []})
    assert resp.status_code == 404
    assert resp.json() == {"error": "用户不存在"}


def test_user_warehouses_put_403_cross_tenant(two_tenants):
    other = _seed_user_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/users/{other}/warehouses", json={"warehouse_ids": []})
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权访问其他租户的用户"}


def test_user_put_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/users/{NONEXISTENT_ID}", json={"display_name": "x"})
    assert resp.status_code == 404
    assert resp.json() == {"error": "用户不存在"}


def test_user_put_403_cross_tenant(two_tenants):
    other = _seed_user_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/users/{other}", json={"display_name": "x"})
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权操作其他租户的用户"}


def test_user_delete_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/users/{NONEXISTENT_ID}")
    assert resp.status_code == 404
    assert resp.json() == {"error": "用户不存在"}


def test_user_delete_403_cross_tenant(two_tenants):
    other = _seed_user_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/users/{other}")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权操作其他租户的用户"}


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

def test_api_key_delete_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/api-keys/{NONEXISTENT_ID}")
    assert resp.status_code == 404
    assert resp.json() == {"error": "API密钥不存在"}


def test_api_key_delete_403_cross_tenant(two_tenants):
    other = _seed_api_key_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/api-keys/{other}")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权操作其他租户的API密钥"}


def test_api_key_status_put_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/api-keys/{NONEXISTENT_ID}/status", json={"disabled": True})
    assert resp.status_code == 404
    assert resp.json() == {"error": "API密钥不存在"}


def test_api_key_status_put_403_cross_tenant(two_tenants):
    other = _seed_api_key_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/api-keys/{other}/status", json={"disabled": True})
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权操作其他租户的API密钥"}


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def test_contact_get_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.get(f"/api/contacts/{NONEXISTENT_ID}")
    assert resp.status_code == 404
    assert resp.json() == {"error": "联系方不存在"}


def test_contact_get_403_cross_tenant(two_tenants):
    other = _seed_contact_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.get(f"/api/contacts/{other}")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权访问该联系方"}


def test_contact_put_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/contacts/{NONEXISTENT_ID}", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json() == {"error": "联系方不存在"}


def test_contact_put_403_cross_tenant(two_tenants):
    other = _seed_contact_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/contacts/{other}", json={"name": "x"})
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权访问该联系方"}


def test_contact_delete_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/contacts/{NONEXISTENT_ID}")
    assert resp.status_code == 404
    assert resp.json() == {"error": "联系方不存在"}


def test_contact_delete_403_cross_tenant(two_tenants):
    other = _seed_contact_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/contacts/{other}")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权访问该联系方"}


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

def test_mcp_put_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/mcp/connections/no-such-id", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json() == {"error": "连接不存在"}


def test_mcp_put_403_cross_tenant(two_tenants):
    other = _seed_mcp_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/mcp/connections/{other}", json={"name": "x"})
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权访问其他租户的MCP连接"}


def test_mcp_delete_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/mcp/connections/no-such-id")
    assert resp.status_code == 404
    assert resp.json() == {"error": "连接不存在"}


def test_mcp_delete_403_cross_tenant(two_tenants):
    other = _seed_mcp_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/mcp/connections/{other}")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权访问其他租户的MCP连接"}


def test_mcp_logs_get_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.get(f"/api/mcp/connections/no-such-id/logs")
    assert resp.status_code == 404
    assert resp.json() == {"error": "连接不存在"}


def test_mcp_logs_get_403_cross_tenant(two_tenants):
    other = _seed_mcp_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.get(f"/api/mcp/connections/{other}/logs")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权访问其他租户的MCP连接"}


# ---------------------------------------------------------------------------
# ERP providers
# (Only the deactivate endpoint uses the inline 404/403 pair we are migrating.
# The other ERP routes call ``_ensure_provider_tenant`` which is a separate
# helper — left untouched per spec.)
# ---------------------------------------------------------------------------

def test_erp_deactivate_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.post(f"/api/erp/providers/{NONEXISTENT_ID}/deactivate")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Provider 不存在"}


# ---------------------------------------------------------------------------
# Face (subjects: standard 404/403 pair vs resolved tid)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _is_sqlite_backend(), reason="seed helper uses sqlite syntax")
def test_face_subject_put_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/face/subjects/{NONEXISTENT_ID}", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json() == {"error": "人员档案不存在"}


@pytest.mark.skipif(not _is_sqlite_backend(), reason="seed helper uses sqlite syntax")
def test_face_subject_put_403_cross_tenant(two_tenants):
    other = _seed_face_subject_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.put(f"/api/face/subjects/{other}", json={"name": "x"})
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权修改该档案"}


@pytest.mark.skipif(not _is_sqlite_backend(), reason="seed helper uses sqlite syntax")
def test_face_subject_delete_404_missing(two_tenants):
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/face/subjects/{NONEXISTENT_ID}")
    assert resp.status_code == 404
    assert resp.json() == {"error": "人员档案不存在"}


@pytest.mark.skipif(not _is_sqlite_backend(), reason="seed helper uses sqlite syntax")
def test_face_subject_delete_403_cross_tenant(two_tenants):
    other = _seed_face_subject_b(two_tenants)
    c = two_tenants["a"]["client"]
    resp = c.delete(f"/api/face/subjects/{other}")
    assert resp.status_code == 403
    assert resp.json() == {"error": "无权删除该档案"}
