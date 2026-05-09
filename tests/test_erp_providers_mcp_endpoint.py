"""Tests for GET /api/erp/providers/active-for-mcp.

This endpoint replaces a direct sqlite read in MCP that used to leak the first
globally-active ERP Provider across tenants. The endpoint MUST:
    - require auth (401 for guests)
    - tenant-scope its result (only the caller's active provider, never another tenant's)
    - return mode=self_owned when system mode is not external_erp
    - 404 when external_erp mode but caller has no active provider
"""
import uuid

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _as_global_admin():
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()


def _make_tenant_with_user(admin_client, role='admin'):
    suffix = uuid.uuid4().hex[:8]
    t = admin_client.post("/api/tenants", json={
        "slug": f"t-{suffix}", "name": f"Tenant {suffix}",
    })
    assert t.status_code == 200, t.text
    tenant_id = t.json()["id"]

    w = admin_client.post("/api/warehouses", json={
        "slug": f"wh-{suffix}", "name": f"Warehouse {suffix}",
        "tenant_id": tenant_id,
    })
    assert w.status_code == 200, w.text
    warehouse_id = w.json()["id"]

    username = f"user-{suffix}"
    password = "Pass123!"
    u = admin_client.post("/api/users", json={
        "username": username, "password": password,
        "display_name": f"User {suffix}", "role": role,
        "tenant_id": tenant_id,
    })
    assert u.status_code == 200, u.text
    user_id = u.json()["id"]
    admin_client.post(f"/api/users/{user_id}/warehouses",
                      json={"warehouse_ids": [warehouse_id]})
    return tenant_id, warehouse_id, username, password


def _login(app_instance, username, password):
    from fastapi.testclient import TestClient
    c = TestClient(app_instance)
    resp = c.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return c


def _seed_provider(tenant_id, *, is_active=1, provider_name=None, filename=None,
                   config=None):
    """Insert an erp_providers row directly. provider_name must be globally unique."""
    import json
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    pname = provider_name or f"prov_{uuid.uuid4().hex[:8]}"
    fname = filename or f"{pname}.py"
    cur.execute(
        """
        INSERT INTO erp_providers
            (name, provider_name, class_name, filename, config, is_active, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pname,
            pname,
            f"{pname.title()}Provider",
            fname,
            json.dumps(config or {}),
            is_active,
            tenant_id,
        ),
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid, pname, fname


def _set_system_mode(mode):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE system_settings SET value = ? WHERE `key` = 'system_mode'",
        (mode,),
    )
    if cur.rowcount == 0:
        cur.execute(
            "INSERT INTO system_settings (`key`, value) VALUES ('system_mode', ?)",
            (mode,),
        )
    conn.commit()
    conn.close()


def _clear_providers():
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM erp_providers")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_state(test_db):
    """Reset admin tenant + system mode + providers between tests."""
    import os as _os
    _os.environ['DATABASE_PATH'] = test_db
    import database as _database
    _database.DATABASE_PATH = test_db

    from database import get_db_connection

    def _reset():
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET tenant_id = 1 WHERE username = 'admin'")
            cur.execute("DELETE FROM erp_providers")
            cur.execute(
                "UPDATE system_settings SET value = 'self_owned' WHERE `key` = 'system_mode'"
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# 1. Unauthenticated → 401
# ---------------------------------------------------------------------------

def test_active_for_mcp_requires_auth(client):
    resp = client.get("/api/erp/providers/active-for-mcp")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. self_owned mode → mode=self_owned, provider=null
# ---------------------------------------------------------------------------

def test_active_for_mcp_self_owned_mode(admin_client):
    _set_system_mode('self_owned')
    resp = admin_client.get("/api/erp/providers/active-for-mcp")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "self_owned"
    assert data["provider"] is None


# ---------------------------------------------------------------------------
# 3. external_erp but no active provider for caller's tenant → 404
# ---------------------------------------------------------------------------

def test_active_for_mcp_external_no_provider_returns_404(
    admin_client, app_instance, monkeypatch
):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin()

    t1, _, u1, p1 = _make_tenant_with_user(admin_client, role='admin')
    # Tenant 2 has an active provider; tenant 1 has nothing
    t2, _, _, _ = _make_tenant_with_user(admin_client, role='admin')
    _seed_provider(t2, is_active=1)

    _set_system_mode('external_erp')

    c1 = _login(app_instance, u1, p1)
    resp = c1.get("/api/erp/providers/active-for-mcp")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 4. external_erp returns the caller-tenant's active provider
# ---------------------------------------------------------------------------

def test_active_for_mcp_returns_own_tenant_provider(
    admin_client, app_instance, monkeypatch
):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin()

    t1, _, u1, p1 = _make_tenant_with_user(admin_client, role='admin')
    pid, pname, fname = _seed_provider(
        t1, is_active=1, config={"endpoint": "https://t1.example.com"}
    )

    _set_system_mode('external_erp')

    c1 = _login(app_instance, u1, p1)
    resp = c1.get("/api/erp/providers/active-for-mcp")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "external_erp"
    assert data["provider"] is not None
    assert data["provider"]["id"] == pid
    assert data["provider"]["provider_name"] == pname
    assert data["provider"]["filename"] == fname
    assert data["provider"]["config"] == {"endpoint": "https://t1.example.com"}


# ---------------------------------------------------------------------------
# 5. CROSS-TENANT LEAK — tenant A must NOT see tenant B's active provider
# ---------------------------------------------------------------------------

def test_active_for_mcp_does_not_leak_other_tenant_provider(
    admin_client, app_instance, monkeypatch
):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin()

    t1, _, u1, p1 = _make_tenant_with_user(admin_client, role='admin')
    t2, _, _, _ = _make_tenant_with_user(admin_client, role='admin')

    # Tenant 2's active provider was inserted FIRST so the old buggy
    # "WHERE is_active = 1 LIMIT 1" would have picked it up for everyone.
    pid_t2, pname_t2, _ = _seed_provider(t2, is_active=1)
    pid_t1, pname_t1, fname_t1 = _seed_provider(t1, is_active=1)

    _set_system_mode('external_erp')

    c1 = _login(app_instance, u1, p1)
    resp = c1.get("/api/erp/providers/active-for-mcp")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "external_erp"
    assert data["provider"] is not None, data
    # Must be tenant 1's row, not tenant 2's
    assert data["provider"]["id"] == pid_t1
    assert data["provider"]["provider_name"] == pname_t1
    assert data["provider"]["filename"] == fname_t1
    assert data["provider"]["provider_name"] != pname_t2, (
        f"cross-tenant leak: tenant 1 saw tenant 2's provider {pname_t2}"
    )
