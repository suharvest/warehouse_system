"""
Regression safety net for warehouse PUT/DELETE (backend/app.py:630, :676).

Pins down the *current* behavior so the upcoming SQLAlchemy/MySQL migration
can prove zero-regression.

Notable current behavior pinned here:
- DELETE is a soft delete (sets is_disabled=1), not a hard row delete.
- Cannot delete the default warehouse (HTTP 400).
- Tenant admin cannot modify a warehouse owned by another tenant (HTTP 403).
"""
import uuid

import pytest


@pytest.fixture(autouse=True)
def _reset_admin_tenant(test_db):
    """Re-pin admin to tenant_id=1 around every test."""
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


_created_wh_ids = []


def _create_warehouse(admin_client, suffix=None, tenant_id=None):
    suffix = suffix or uuid.uuid4().hex[:6]
    payload = {"slug": f"wh-{suffix}", "name": f"WH {suffix}",
               "address": "addr"}
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    resp = admin_client.post("/api/warehouses", json=payload)
    assert resp.status_code == 200, resp.text
    info = resp.json()
    _created_wh_ids.append(info['id'])
    return info


@pytest.fixture(autouse=True)
def _disable_created_warehouses_after():
    """After each test, disable every warehouse this module created so the
    `infer_single_writable_warehouse_id` invariant other tests rely on is
    preserved across the session-scoped DB."""
    yield
    if not _created_wh_ids:
        return
    try:
        from database import get_db_connection
        cn = get_db_connection()
        cu = cn.cursor()
        for wid in _created_wh_ids:
            cu.execute("UPDATE warehouses SET is_disabled = 1 WHERE id = ?",
                       (wid,))
        cn.commit()
        cn.close()
    except Exception:
        pass
    _created_wh_ids.clear()


# ---------------------------------------------------------------------------
# PUT /api/warehouses/{id}
# ---------------------------------------------------------------------------

def test_admin_can_rename_warehouse(admin_client):
    wh = _create_warehouse(admin_client)
    new_name = f"Renamed-{uuid.uuid4().hex[:6]}"
    resp = admin_client.put(f"/api/warehouses/{wh['id']}",
                            json={"name": new_name})
    assert resp.status_code == 200, resp.text
    assert resp.json()['name'] == new_name


def test_update_nonexistent_warehouse_404(admin_client):
    resp = admin_client.put("/api/warehouses/99999999",
                            json={"name": "x"})
    assert resp.status_code == 404


def test_cannot_disable_default_warehouse(admin_client, default_warehouse_id):
    resp = admin_client.put(f"/api/warehouses/{default_warehouse_id}",
                            json={"is_disabled": True})
    assert resp.status_code == 400
    assert "默认" in resp.text


# ---------------------------------------------------------------------------
# DELETE /api/warehouses/{id}  (soft delete: is_disabled=1)
# ---------------------------------------------------------------------------

def test_delete_warehouse_is_soft_delete(admin_client):
    """Pin down: DELETE just sets is_disabled=1; the row is not removed."""
    wh = _create_warehouse(admin_client)

    resp = admin_client.delete(f"/api/warehouses/{wh['id']}")
    assert resp.status_code == 200, resp.text

    # Row still exists (soft delete) — verify via DB
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_disabled FROM warehouses WHERE id = ?", (wh['id'],))
    row = cur.fetchone()
    conn.close()
    assert row is not None, "DELETE should soft-delete (row still present)"
    assert row['is_disabled'] == 1, (
        f"DELETE should set is_disabled=1, got {row['is_disabled']}")

    # And it must not appear in the default list (which excludes disabled)
    resp = admin_client.get("/api/warehouses")
    assert resp.status_code == 200
    ids = [w['id'] for w in resp.json()]
    assert wh['id'] not in ids


def test_delete_warehouse_with_materials_current_behavior(admin_client):
    """Pin down: warehouse delete (soft) succeeds even when the warehouse
    contains materials. This is what the code currently does — log it so the
    SA migration cannot accidentally change it.
    """
    wh = _create_warehouse(admin_client)

    # Seed a material into the warehouse
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, "
        "safe_stock, location, warehouse_id, tenant_id) "
        "VALUES (?, ?, 'T', 5, 'pcs', 1, '', ?, 1)",
        (f"Mat-{uuid.uuid4().hex[:6]}",
         f"S-{uuid.uuid4().hex[:6]}", wh['id']))
    conn.commit()
    conn.close()

    resp = admin_client.delete(f"/api/warehouses/{wh['id']}")
    # Current behavior: success (no FK protection on soft-delete).
    assert resp.status_code == 200, (
        f"Pinned-down behavior changed: warehouse delete with materials no "
        f"longer succeeds. Decide if that's intentional. Response: {resp.text}"
    )


def test_cannot_delete_default_warehouse(admin_client, default_warehouse_id):
    resp = admin_client.delete(f"/api/warehouses/{default_warehouse_id}")
    assert resp.status_code == 400
    assert "默认" in resp.text


def test_delete_nonexistent_warehouse_404(admin_client):
    resp = admin_client.delete("/api/warehouses/99999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cross-tenant: tenant admin cannot modify another tenant's warehouse
# ---------------------------------------------------------------------------

def test_tenant_admin_cannot_modify_other_tenant_warehouse(
        admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")

    # Promote admin to global to set up two tenants
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()

    suffix = uuid.uuid4().hex[:6]
    t_a = admin_client.post("/api/tenants", json={
        "slug": f"wta-{suffix}", "name": f"WTA{suffix}"}).json()['id']
    t_b = admin_client.post("/api/tenants", json={
        "slug": f"wtb-{suffix}", "name": f"WTB{suffix}"}).json()['id']
    wh_b = admin_client.post("/api/warehouses", json={
        "slug": f"wha-{suffix}", "name": f"WHA{suffix}",
        "tenant_id": t_b}).json()['id']

    # Tenant A admin user
    user_a = f"wua-{suffix}"
    admin_client.post("/api/users", json={
        "username": user_a, "password": "Pass123!",
        "display_name": "WUA", "role": "admin", "tenant_id": t_a,
    })

    from fastapi.testclient import TestClient
    c = TestClient(app_instance)
    r = c.post("/api/auth/login",
               json={"username": user_a, "password": "Pass123!"})
    assert r.status_code == 200

    # PUT another tenant's warehouse
    resp = c.put(f"/api/warehouses/{wh_b}", json={"name": "Hijack"})
    assert resp.status_code == 403, resp.text

    # DELETE another tenant's warehouse
    resp = c.delete(f"/api/warehouses/{wh_b}")
    assert resp.status_code == 403, resp.text
