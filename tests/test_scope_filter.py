"""R4 regression: list endpoints honour tenant scope.

Asserts that:
  - global admin (tenant_id=None) sees rows from BOTH tenants
  - tenant-scoped admin sees only rows of their own tenant

This file is the safety net for the R4 refactor (route inline tenant filters
through ``build_scope_predicates``). It must pass against pre-R4 code (baseline)
AND post-R4 code (no semantic drift).
"""
import uuid

import pytest
from fastapi.testclient import TestClient


# ---------------------- helpers (mirrors test_tenants.py) ----------------------

def _as_global_admin(admin_client):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()


def _create_tenant_admin_with_warehouse(admin_client, suffix):
    tenant_resp = admin_client.post("/api/tenants", json={
        "slug": f"tenant-sf-{suffix}",
        "name": f"Tenant SF {suffix}",
    })
    assert tenant_resp.status_code == 200, tenant_resp.text
    tenant_id = tenant_resp.json()["id"]

    wh_resp = admin_client.post("/api/warehouses", json={
        "slug": f"wh-sf-{suffix}",
        "name": f"Warehouse SF {suffix}",
        "tenant_id": tenant_id,
    })
    assert wh_resp.status_code == 200, wh_resp.text
    warehouse_id = wh_resp.json()["id"]

    username = f"tenant-sf-admin-{suffix}"
    user_resp = admin_client.post("/api/users", json={
        "username": username,
        "password": "Admin123!",
        "display_name": "Scope Filter Admin",
        "role": "admin",
        "tenant_id": tenant_id,
    })
    assert user_resp.status_code == 200, user_resp.text
    return tenant_id, warehouse_id, username


def _login_as(app_instance, username, password="Admin123!"):
    c = TestClient(app_instance)
    resp = c.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True, resp.text
    return c


@pytest.fixture
def two_tenants(admin_client, app_instance, monkeypatch):
    """Create two distinct tenants, each with admin + warehouse, plus a global admin client."""
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)

    suffix = uuid.uuid4().hex[:8]
    t_a, wh_a, admin_a_name = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-a")
    t_b, wh_b, admin_b_name = _create_tenant_admin_with_warehouse(admin_client, f"{suffix}-b")

    # Seed one contact per tenant via the global admin (tenant_id explicit).
    for tid in (t_a, t_b):
        resp = admin_client.post("/api/contacts", json={
            "name": f"Contact-{tid}-{suffix}",
            "is_supplier": True,
            "is_customer": True,
            "tenant_id": tid,
        })
        assert resp.status_code == 200, resp.text

    return {
        "global_admin": admin_client,
        "tenant_a": {
            "id": t_a, "warehouse_id": wh_a,
            "client": _login_as(app_instance, admin_a_name),
        },
        "tenant_b": {
            "id": t_b, "warehouse_id": wh_b,
            "client": _login_as(app_instance, admin_b_name),
        },
        "suffix": suffix,
    }


def _tenant_ids(rows):
    """Extract tenant_id from a list response (tolerates rows without the field)."""
    out = set()
    for row in rows:
        if isinstance(row, dict) and "tenant_id" in row:
            out.add(row["tenant_id"])
    return out


# ---------------------- list-endpoint scope tests ----------------------

# Endpoints whose row payloads expose `tenant_id`.
_PATHS_WITH_TENANT_ID = ["/api/warehouses", "/api/users"]


@pytest.mark.parametrize("path", _PATHS_WITH_TENANT_ID)
def test_global_admin_sees_all_tenants(two_tenants, path):
    """Global admin (tenant_id=None) must see rows belonging to BOTH tenants."""
    g = two_tenants["global_admin"]
    t_a = two_tenants["tenant_a"]["id"]
    t_b = two_tenants["tenant_b"]["id"]

    resp = g.get(path)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    rows = payload if isinstance(payload, list) else payload.get("items", [])

    seen = _tenant_ids(rows)
    assert t_a in seen, f"global admin missing tenant_a={t_a} in {path}: {seen}"
    assert t_b in seen, f"global admin missing tenant_b={t_b} in {path}: {seen}"


@pytest.mark.parametrize("path", _PATHS_WITH_TENANT_ID)
def test_scoped_admin_sees_only_own_tenant(two_tenants, path):
    """Scoped tenant admin must NEVER see rows from another tenant."""
    t_a = two_tenants["tenant_a"]
    t_b_id = two_tenants["tenant_b"]["id"]

    resp = t_a["client"].get(path)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    rows = payload if isinstance(payload, list) else payload.get("items", [])

    seen = _tenant_ids(rows)
    assert t_b_id not in seen, f"tenant_a leaked tenant_b={t_b_id} in {path}: {seen}"
    if seen:
        assert seen == {t_a["id"]}, f"tenant_a unexpected tenant_ids in {path}: {seen}"


# /api/contacts response items don't expose tenant_id; assert by seeded names.
def test_contacts_global_admin_sees_both(two_tenants):
    g = two_tenants["global_admin"]
    suffix = two_tenants["suffix"]
    t_a = two_tenants["tenant_a"]["id"]
    t_b = two_tenants["tenant_b"]["id"]
    resp = g.get("/api/contacts")
    assert resp.status_code == 200, resp.text
    names = {item["name"] for item in resp.json().get("items", [])}
    assert f"Contact-{t_a}-{suffix}" in names
    assert f"Contact-{t_b}-{suffix}" in names


def test_contacts_scoped_admin_only_own(two_tenants):
    t_a = two_tenants["tenant_a"]
    t_b_id = two_tenants["tenant_b"]["id"]
    suffix = two_tenants["suffix"]
    resp = t_a["client"].get("/api/contacts")
    assert resp.status_code == 200, resp.text
    names = {item["name"] for item in resp.json().get("items", [])}
    assert f"Contact-{t_b_id}-{suffix}" not in names
    assert f"Contact-{t_a['id']}-{suffix}" in names


def test_suppliers_dropdown_scoped(two_tenants):
    """/api/contacts/suppliers — must hide cross-tenant suppliers."""
    t_a = two_tenants["tenant_a"]
    t_b_id = two_tenants["tenant_b"]["id"]
    suffix = two_tenants["suffix"]

    resp = t_a["client"].get("/api/contacts/suppliers")
    assert resp.status_code == 200, resp.text
    names = {r["name"] for r in resp.json()}
    assert f"Contact-{t_b_id}-{suffix}" not in names


def test_customers_dropdown_scoped(two_tenants):
    """/api/contacts/customers — must hide cross-tenant customers."""
    t_a = two_tenants["tenant_a"]
    t_b_id = two_tenants["tenant_b"]["id"]
    suffix = two_tenants["suffix"]

    resp = t_a["client"].get("/api/contacts/customers")
    assert resp.status_code == 200, resp.text
    names = {r["name"] for r in resp.json()}
    assert f"Contact-{t_b_id}-{suffix}" not in names


def test_operators_dropdown_scoped(two_tenants):
    """/api/contacts/operators — must hide cross-tenant admin/operate users."""
    t_a = two_tenants["tenant_a"]
    other_admin_username = None
    for r in two_tenants["global_admin"].get("/api/users").json():
        if r["tenant_id"] == two_tenants["tenant_b"]["id"]:
            other_admin_username = r["username"]
            break
    assert other_admin_username is not None

    resp = t_a["client"].get("/api/operators")
    assert resp.status_code == 200, resp.text
    usernames = {r["username"] for r in resp.json()}
    assert other_admin_username not in usernames


def test_global_admin_sees_all_tenants_route(two_tenants):
    """/api/tenants for global admin must list both seeded tenants."""
    g = two_tenants["global_admin"]
    t_a = two_tenants["tenant_a"]["id"]
    t_b = two_tenants["tenant_b"]["id"]
    resp = g.get("/api/tenants")
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert t_a in ids and t_b in ids


def test_scoped_admin_sees_only_own_tenant_in_tenants_route(two_tenants):
    """/api/tenants for scoped admin must show only own tenant."""
    t_a = two_tenants["tenant_a"]
    t_b_id = two_tenants["tenant_b"]["id"]
    resp = t_a["client"].get("/api/tenants")
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert ids == {t_a["id"]}
    assert t_b_id not in ids


# ---------------------- row-ownership assertion (R4 extension) ----------------------

def test_erp_provider_cross_tenant_forbidden(two_tenants):
    """Seed an ERP provider belonging to tenant A directly in DB; tenant B
    accessing it must get 403. Exercises ``_ensure_provider_tenant`` (row
    ownership assertion site in spec R4 inventory)."""
    from datetime import datetime as _dt
    from db import get_engine
    from sqlalchemy import insert as _insert
    from metadata import erp_providers as _erp

    t_a = two_tenants["tenant_a"]
    t_b = two_tenants["tenant_b"]
    suffix = two_tenants["suffix"]
    now = _dt.now()

    with get_engine().begin() as conn:
        result = conn.execute(_insert(_erp).values(
            name=f"prov-a-{suffix}",
            provider_name=f"prov_a_{suffix}",
            class_name="DummyProvider",
            filename=f"prov_a_{suffix}.py",
            tenant_id=t_a["id"],
            created_at=now,
            updated_at=now,
        ))
        provider_id = result.inserted_primary_key[0]

    # Tenant B trying to delete or activate -> _ensure_provider_tenant fires 403
    cross = t_b["client"].delete(f"/api/erp/providers/{provider_id}")
    assert cross.status_code == 403, cross.text
