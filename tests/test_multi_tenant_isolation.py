"""
Multi-tenant isolation regression tests.

Covers the invariants enforced by the audit-driven fixes:
    1. Dashboard endpoints require authentication.
    2. /api/fuzzy-match requires auth and is scoped per tenant.
    3. Tenant A cannot see tenant B's contacts via /api/contacts.
    4. Global admin write paths reject ambiguous tenant defaults.
    5. stock_in rejects a contact_id belonging to another tenant.
    6. preview_import_excel resolves existing tenant-level contacts (B1 regression).

Each test is independent; they use the shared admin_client + fixtures from
conftest.py, plus locally-created tenants/users.
"""
import uuid
import importlib
from io import BytesIO

import pytest
from openpyxl import Workbook


@pytest.fixture(autouse=True)
def _isolate_admin_tenant(test_db):
    """Each isolation test mutates the conftest admin (promotes to global admin
    or creates extra tenants). Reset the admin's tenant_id back to 1 after each
    test so we don't poison subsequent test files that share the session DB.

    Also defensive against poisoning by test_face.py which monkeypatches
    DATABASE_PATH to a temp DB — when its fixture tears down, our DB connection
    helpers may still resolve to the polluted path. Re-pin the env to the
    session test_db before every test.
    """
    import os as _os
    _os.environ['DATABASE_PATH'] = test_db
    # test_face.py reloads `database` while its monkeypatched DATABASE_PATH
    # is active, leaving the module-level `database.DATABASE_PATH` pointing
    # at a temp file that no longer exists. Re-pin the module variable to
    # the session test DB before each isolation test.
    import database as _database
    _database.DATABASE_PATH = test_db
    from database import get_db_connection

    def _reset():
        try:
            conn = get_db_connection()
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
# helpers
# ---------------------------------------------------------------------------

def _as_global_admin():
    """Promote the conftest admin to a global admin (tenant_id = NULL)."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()


def _make_tenant_with_user(admin_client, role='operate'):
    """Create a fresh tenant + warehouse + admin/operate user for that tenant.
    Returns (tenant_id, warehouse_id, username, password).
    """
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

    # Grant the user access to the warehouse
    admin_client.post(f"/api/users/{user_id}/warehouses",
                      json={"warehouse_ids": [warehouse_id]})

    return tenant_id, warehouse_id, username, password


def _login(app_instance, username, password):
    from fastapi.testclient import TestClient
    c = TestClient(app_instance)
    resp = c.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json().get("success") is True, resp.json()
    return c


def _seed_contact(tenant_id, name, *, is_supplier=True, is_customer=False):
    """Insert a tenant-level contact directly (bypass API for setup speed)."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO contacts (name, is_supplier, is_customer, tenant_id, warehouse_id, created_at)
        VALUES (?, ?, ?, ?, NULL, datetime('now'))
    ''', (name, 1 if is_supplier else 0, 1 if is_customer else 0, tenant_id))
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    return cid


def _seed_material(tenant_id, warehouse_id, name, sku):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location,
                               warehouse_id, tenant_id)
        VALUES (?, ?, 'Test', 100, 'pcs', 10, '', ?, ?)
    ''', (name, sku, warehouse_id, tenant_id))
    mid = cur.lastrowid
    conn.commit()
    conn.close()
    return mid


# ---------------------------------------------------------------------------
# 1. Dashboard endpoints require auth (B2)
# ---------------------------------------------------------------------------

def test_dashboard_endpoints_reject_unauthenticated(client):
    """Guests must not be able to read aggregate stats — they leaked across all
    tenants in multi_tenant mode before the fix."""
    for path in (
        "/api/dashboard/stats",
        "/api/dashboard/category-distribution",
        "/api/dashboard/weekly-trend",
        "/api/dashboard/top-stock",
        "/api/dashboard/low-stock-alert",
    ):
        resp = client.get(path)
        assert resp.status_code == 401, f"{path} should require auth, got {resp.status_code}"


# ---------------------------------------------------------------------------
# 2. /api/fuzzy-match requires auth + tenant-scoped (R2)
# ---------------------------------------------------------------------------

def test_fuzzy_match_requires_auth(client):
    resp = client.get("/api/fuzzy-match", params={"q": "anything"})
    assert resp.status_code == 401


def test_fuzzy_match_scoped_to_tenant(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin()

    t1, w1, u1, p1 = _make_tenant_with_user(admin_client, role='admin')
    t2, w2, u2, p2 = _make_tenant_with_user(admin_client, role='admin')

    # Each tenant has a uniquely-named material
    name1 = f"WidgetA-{uuid.uuid4().hex[:6]}"
    name2 = f"WidgetB-{uuid.uuid4().hex[:6]}"
    _seed_material(t1, w1, name1, sku=name1)
    _seed_material(t2, w2, name2, sku=name2)

    # Bust the matcher cache so the new rows show up
    import app as app_module
    app_module.get_fuzzy_matcher().invalidate_cache()

    c1 = _login(app_instance, u1, p1)
    # Tenant 1 user searches by tenant 2's material name → should not see it
    resp = c1.get("/api/fuzzy-match", params={"q": name2, "threshold": 70})
    assert resp.status_code == 200
    cands = resp.json().get("candidates", [])
    leaked = [c for c in cands if c.get("name") == name2]
    assert leaked == [], f"tenant 1 saw tenant 2's material in fuzzy results: {cands}"


# ---------------------------------------------------------------------------
# 3. Cross-tenant contact list isolation (R8)
# ---------------------------------------------------------------------------

def test_contacts_isolation_between_tenants(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin()

    t1, _, u1, p1 = _make_tenant_with_user(admin_client, role='admin')
    t2, _, _, _ = _make_tenant_with_user(admin_client, role='admin')

    name1 = f"Acme-{uuid.uuid4().hex[:6]}"
    name2 = f"Globex-{uuid.uuid4().hex[:6]}"
    _seed_contact(t1, name1, is_supplier=True)
    _seed_contact(t2, name2, is_supplier=True)

    c1 = _login(app_instance, u1, p1)

    # /api/contacts (tenant-scoped after R8)
    resp = c1.get("/api/contacts", params={"page_size": 100})
    assert resp.status_code == 200
    names = {x["name"] for x in resp.json()["items"]}
    assert name1 in names
    assert name2 not in names, f"tenant 1 saw tenant 2 contact: {names}"

    # /api/contacts/suppliers (used by stock_in dropdown)
    resp = c1.get("/api/contacts/suppliers")
    assert resp.status_code == 200
    names = {x["name"] for x in resp.json()}
    assert name1 in names and name2 not in names


# ---------------------------------------------------------------------------
# 4. Global admin must specify tenant/warehouse on writes (R3, R12)
# ---------------------------------------------------------------------------

def test_global_admin_create_api_key_without_warehouse_rejected(admin_client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin()

    resp = admin_client.post("/api/api-keys", json={
        "name": f"k-{uuid.uuid4().hex[:6]}",
        "role": "operate",
        # warehouse_id omitted on purpose
    })
    assert resp.status_code == 400
    assert "warehouse_id" in resp.text


def test_global_admin_create_contact_without_tenant_rejected(admin_client, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin()

    resp = admin_client.post("/api/contacts", json={
        "name": f"c-{uuid.uuid4().hex[:6]}",
        "is_supplier": True,
        "is_customer": False,
        # tenant_id omitted
    })
    assert resp.status_code == 400
    assert "tenant_id" in resp.text


# ---------------------------------------------------------------------------
# 5. stock_in rejects cross-tenant contact_id (P2)
# ---------------------------------------------------------------------------

def test_stock_in_rejects_cross_tenant_contact_id(admin_client, app_instance, monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin()

    # Use admin role so the user auto-gets access to their tenant's warehouse
    t1, w1, u1, p1 = _make_tenant_with_user(admin_client, role='admin')
    t2, _, _, _ = _make_tenant_with_user(admin_client, role='admin')

    # Tenant 1 has a material; tenant 2 has a contact
    mat_name = f"M-{uuid.uuid4().hex[:6]}"
    _seed_material(t1, w1, mat_name, sku=mat_name)
    cross_contact_id = _seed_contact(t2, f"Foreign-{uuid.uuid4().hex[:6]}")

    c1 = _login(app_instance, u1, p1)
    resp = c1.post("/api/materials/stock-in", json={
        "product_name": mat_name,
        "quantity": 1,
        "reason_category": "purchase",
        "warehouse_id": w1,
        "contact_id": cross_contact_id,
    })
    # Should be rejected with 403/400 — never silently succeed
    assert resp.status_code in (400, 403), resp.text
    assert "联系方" in resp.text or "tenant" in resp.text.lower()


# ---------------------------------------------------------------------------
# 6. preview_import_excel finds existing tenant-level contacts (B1)
# ---------------------------------------------------------------------------

def _build_import_excel(rows):
    """Build a minimal Excel file matching the simplified-import format."""
    wb = Workbook()
    ws = wb.active
    ws.append(["名称", "SKU", "分类", "库存数量", "单位", "安全库存", "位置", "联系方"])
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_import_preview_resolves_existing_tenant_contact(admin_client, default_warehouse_id):
    """After R8/B1 the preview must NOT classify an existing contact as new."""
    contact_name = f"PreCo-{uuid.uuid4().hex[:6]}"
    sku = f"PRE-{uuid.uuid4().hex[:6]}"
    name = f"Material {sku}"

    # Default-tenant contact (single-tenant mode → tenant_id = 1)
    _seed_contact(1, contact_name, is_supplier=True)

    excel = _build_import_excel([
        [name, sku, "Test", 5, "pcs", 1, "A-1", contact_name],
    ])

    resp = admin_client.post(
        "/api/materials/import-excel/preview",
        params={"warehouse_id": default_warehouse_id},
        files={"file": ("import.xlsx", excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("success") is True, data
    # The preview must have resolved the existing contact (not treated as new)
    new_contacts = []
    for item in data.get("preview", []):
        if item.get("contact_name") == contact_name and item.get("contact_id") is None:
            new_contacts.append(item)
    assert new_contacts == [], (
        f"existing contact {contact_name} was misclassified as new: {new_contacts}"
    )
