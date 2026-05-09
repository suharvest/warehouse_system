"""
Regression safety net for the X-API-Key auth path (backend/app.py:328-403).

These tests pin down the *current* behavior so the upcoming SQLAlchemy/MySQL
migration can prove zero-regression. Do NOT change behavior in production
code based on these tests — write the test, then file separately if a
discrepancy needs fixing.

Coverage:
- Each role (admin / operate / view) reaches its allowed endpoints, denied
  at the next-higher one.
- Disabled key → 401.
- Wrong / missing key → 401.
- API key with warehouse_id is scoped to that warehouse.
- API key tied to tenant A cannot access tenant B's data.
"""
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_admin_tenant(test_db):
    """Defensively re-pin the admin user back to tenant_id=1 around each test
    (other test files promote admin to global admin and may leak)."""
    import os as _os
    _os.environ['DATABASE_PATH'] = test_db
    import database as _database
    _database.DATABASE_PATH = test_db

    def _reset():
        try:
            conn = _database.get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET tenant_id = 1 WHERE username = 'admin'")
            conn.commit()
            conn.close()
        except Exception:
            pass

    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_api_key(admin_client, *, role='operate', warehouse_id=None,
                    name=None):
    """Use the public endpoint so the key_hash is consistent with prod."""
    payload = {"name": name or f"k-{uuid.uuid4().hex[:6]}", "role": role}
    if warehouse_id is not None:
        payload["warehouse_id"] = warehouse_id
    resp = admin_client.post("/api/api-keys", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()  # contains id, key, ...


def _make_tenant_with_warehouse(admin_client):
    """Promote admin to global, create tenant + warehouse, return ids.
    Caller is responsible for restoring admin tenant via the autouse fixture."""
    from database import get_db_connection
    # Promote temporarily
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()

    suffix = uuid.uuid4().hex[:8]
    t = admin_client.post("/api/tenants",
                          json={"slug": f"t-{suffix}", "name": f"T{suffix}"})
    assert t.status_code == 200, t.text
    tenant_id = t.json()["id"]
    w = admin_client.post("/api/warehouses", json={
        "slug": f"wh-{suffix}", "name": f"WH{suffix}", "tenant_id": tenant_id,
    })
    assert w.status_code == 200, w.text
    warehouse_id = w.json()["id"]
    return tenant_id, warehouse_id


def _new_client(app_instance):
    return TestClient(app_instance)


def _key_headers(api_key):
    return {"X-API-Key": api_key}


# ---------------------------------------------------------------------------
# 1. Missing / wrong / disabled key behavior
# ---------------------------------------------------------------------------

def test_no_auth_returns_401(client):
    """No Bearer / cookie / X-API-Key → guest → require_auth rejects."""
    resp = client.get("/api/users")
    assert resp.status_code == 401


def test_wrong_api_key_falls_through_to_guest(app_instance):
    """An unknown X-API-Key is not a hard error — it falls through to guest
    auth and require_auth produces 401."""
    c = _new_client(app_instance)
    resp = c.get("/api/users", headers=_key_headers("definitely-not-a-real-key"))
    assert resp.status_code == 401


def test_disabled_api_key_falls_through_to_guest(admin_client, app_instance):
    """Disabled key (`is_disabled=1`) is filtered out by the SELECT, so it
    falls through to guest → 401."""
    info = _create_api_key(admin_client, role='admin')
    # Disable it
    resp = admin_client.put(f"/api/api-keys/{info['id']}/status",
                            json={"disabled": True})
    assert resp.status_code == 200, resp.text

    c = _new_client(app_instance)
    resp = c.get("/api/users", headers=_key_headers(info['key']))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Role-based access via API key
# ---------------------------------------------------------------------------

def test_admin_api_key_can_list_users(admin_client, app_instance):
    info = _create_api_key(admin_client, role='admin')
    c = _new_client(app_instance)
    resp = c.get("/api/users", headers=_key_headers(info['key']))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_operate_api_key_cannot_list_users(admin_client, app_instance):
    info = _create_api_key(admin_client, role='operate')
    c = _new_client(app_instance)
    resp = c.get("/api/users", headers=_key_headers(info['key']))
    assert resp.status_code == 403


def test_operate_api_key_can_stock_in(admin_client, app_instance,
                                     sample_material, default_warehouse_id):
    info = _create_api_key(admin_client, role='operate',
                           warehouse_id=default_warehouse_id)
    c = _new_client(app_instance)
    resp = c.post("/api/materials/stock-in",
                  headers=_key_headers(info['key']),
                  json={
                      "product_name": sample_material['name'],
                      "quantity": 1,
                      "reason_category": "purchase",
                      "warehouse_id": default_warehouse_id,
                  })
    assert resp.status_code == 200, resp.text
    assert resp.json()['success'] is True


def test_view_api_key_can_list_materials(admin_client, app_instance,
                                         default_warehouse_id):
    info = _create_api_key(admin_client, role='view',
                           warehouse_id=default_warehouse_id)
    c = _new_client(app_instance)
    resp = c.get("/api/materials/all", headers=_key_headers(info['key']))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_view_api_key_cannot_stock_in(admin_client, app_instance,
                                      sample_material, default_warehouse_id):
    info = _create_api_key(admin_client, role='view',
                           warehouse_id=default_warehouse_id)
    c = _new_client(app_instance)
    resp = c.post("/api/materials/stock-in",
                  headers=_key_headers(info['key']),
                  json={
                      "product_name": sample_material['name'],
                      "quantity": 1,
                      "reason_category": "purchase",
                      "warehouse_id": default_warehouse_id,
                  })
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. warehouse_id scoping on the key
# ---------------------------------------------------------------------------

def test_api_key_with_warehouse_cannot_access_other_warehouse(
        admin_client, app_instance, default_warehouse_id):
    """If a key is bound to wh A, requesting wh B (in same tenant) should be
    rejected by check_warehouse_access (403) on writes.

    We create a second warehouse in the same tenant, key bound to default,
    and try to stock-in to the second warehouse — should 403."""
    suffix = uuid.uuid4().hex[:6]
    w = admin_client.post("/api/warehouses", json={
        "slug": f"wh-{suffix}", "name": f"WH{suffix}",
    })
    assert w.status_code == 200, w.text
    other_wh = w.json()["id"]

    try:
        # Create material in the other warehouse so stock-in resolves
        from database import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        sku = f"X-{uuid.uuid4().hex[:6]}"
        cur.execute(
            "INSERT INTO materials (name, sku, category, quantity, unit, "
            "safe_stock, location, warehouse_id, tenant_id) "
            "VALUES (?, ?, 'T', 0, 'pcs', 1, '', ?, 1)",
            (f"M-{sku}", sku, other_wh))
        conn.commit()
        conn.close()

        info = _create_api_key(admin_client, role='operate',
                               warehouse_id=default_warehouse_id)

        c = _new_client(app_instance)
        resp = c.post("/api/materials/stock-in",
                      headers=_key_headers(info['key']),
                      json={
                          "product_name": f"M-{sku}",
                          "quantity": 1,
                          "reason_category": "purchase",
                          "warehouse_id": other_wh,
                      })
        # check_warehouse_access raises 403 when key warehouse mismatches
        assert resp.status_code == 403, resp.text
    finally:
        # Disable extra warehouse so infer_single_writable_warehouse_id stays
        # at one accessible warehouse for tenant 1 in the session DB.
        from database import get_db_connection
        cn = get_db_connection()
        cu = cn.cursor()
        cu.execute("UPDATE warehouses SET is_disabled = 1 WHERE id = ?",
                   (other_wh,))
        cn.commit()
        cn.close()


def test_api_key_warehouse_filters_records(admin_client, app_instance,
                                           default_warehouse_id,
                                           sample_material):
    """A key bound to a warehouse should only see records for that wh
    when no warehouse_id is supplied (resolve_warehouse_id falls back to
    key.warehouse_id)."""
    # Generate a record in default warehouse
    admin_client.post("/api/materials/stock-in", json={
        "product_name": sample_material['name'],
        "quantity": 1,
        "reason_category": "purchase",
        "warehouse_id": default_warehouse_id,
    })

    info = _create_api_key(admin_client, role='view',
                           warehouse_id=default_warehouse_id)
    c = _new_client(app_instance)
    resp = c.get("/api/inventory/records",
                 headers=_key_headers(info['key']))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Every returned row must belong to the bound warehouse
    for item in data['items']:
        assert item['warehouse_id'] == default_warehouse_id


# ---------------------------------------------------------------------------
# 4. Cross-tenant isolation via API key
# ---------------------------------------------------------------------------

def test_cross_tenant_api_key_cannot_see_other_tenant_records(
        admin_client, app_instance, monkeypatch):
    """API key tied to tenant A's warehouse should not see tenant B's records
    via /api/inventory/records."""
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")

    # Promote admin and build two tenants with one warehouse each
    tenant_a, wh_a = _make_tenant_with_warehouse(admin_client)
    tenant_b, wh_b = _make_tenant_with_warehouse(admin_client)

    # Seed inventory_records directly into each tenant's warehouse
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    # Material A
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, "
        "safe_stock, warehouse_id, tenant_id) "
        "VALUES ('MA', 'SA', 'T', 10, 'pcs', 1, ?, ?)",
        (wh_a, tenant_a))
    mat_a = cur.lastrowid
    # Material B
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, "
        "safe_stock, warehouse_id, tenant_id) "
        "VALUES ('MB', 'SB', 'T', 10, 'pcs', 1, ?, ?)",
        (wh_b, tenant_b))
    mat_b = cur.lastrowid
    cur.execute(
        "INSERT INTO inventory_records (material_id, type, quantity, operator,"
        " reason_category, warehouse_id, tenant_id, created_at) "
        "VALUES (?, 'in', 1, 'sys', 'purchase', ?, ?, CURRENT_TIMESTAMP)",
        (mat_a, wh_a, tenant_a))
    cur.execute(
        "INSERT INTO inventory_records (material_id, type, quantity, operator,"
        " reason_category, warehouse_id, tenant_id, created_at) "
        "VALUES (?, 'in', 1, 'sys', 'purchase', ?, ?, CURRENT_TIMESTAMP)",
        (mat_b, wh_b, tenant_b))
    conn.commit()
    conn.close()

    # Create an API key scoped to tenant A's warehouse. admin is currently
    # global, so warehouse_id fully determines tenant.
    info = _create_api_key(admin_client, role='view', warehouse_id=wh_a)

    c = _new_client(app_instance)
    resp = c.get("/api/inventory/records", headers=_key_headers(info['key']))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Tenant B records must not leak through tenant A's API key
    for item in data['items']:
        assert item['warehouse_id'] == wh_a
        # if material_name comes through, it should be 'MA' not 'MB'
        if item.get('material_name') == 'MB':
            pytest.fail(f"Tenant B record leaked to tenant A key: {item}")
