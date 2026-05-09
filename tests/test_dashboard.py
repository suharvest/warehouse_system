"""
Dashboard tests: statistics, trends, alerts.
"""
import uuid
from datetime import datetime

import pytest


class TestDashboardStats:
    """Dashboard statistics endpoint tests."""

    def test_get_stats(self, admin_client, sample_material):
        """Dashboard stats should return valid structure."""
        resp = admin_client.get("/api/dashboard/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert 'total_stock' in data
        assert 'material_types' in data
        assert 'today_in' in data
        assert 'today_out' in data
        assert 'low_stock_count' in data
        assert data['material_types'] >= 1

    def test_category_distribution(self, admin_client, sample_material):
        """Category distribution should return list of categories."""
        resp = admin_client.get("/api/dashboard/category-distribution")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            assert 'name' in data[0]
            assert 'value' in data[0]

    def test_weekly_trend(self, admin_client):
        """Weekly trend should return daily in/out data."""
        resp = admin_client.get("/api/dashboard/weekly-trend")
        assert resp.status_code == 200
        data = resp.json()
        assert 'dates' in data
        assert 'in_data' in data
        assert 'out_data' in data
        assert len(data['dates']) == len(data['in_data'])
        assert len(data['dates']) == len(data['out_data'])

    def test_top_stock(self, admin_client, sample_material):
        """Top stock should return names, quantities, categories."""
        resp = admin_client.get("/api/dashboard/top-stock")
        assert resp.status_code == 200
        data = resp.json()
        assert 'names' in data
        assert 'quantities' in data
        assert 'categories' in data
        assert len(data['names']) == len(data['quantities'])

    def test_low_stock_alert(self, admin_client):
        """Low stock alert should return items below safe stock."""
        resp = admin_client.get("/api/dashboard/low-stock-alert")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # If there are alerts, they should have required fields
        for item in data:
            assert 'name' in item
            assert 'quantity' in item
            assert 'safe_stock' in item


class TestDashboardWithData:
    """Dashboard tests that require stock operations to generate data."""

    def test_stats_reflect_stock_operations(self, admin_client, sample_material):
        """Stats should reflect recent stock operations."""
        # Get initial stats
        initial = admin_client.get("/api/dashboard/stats").json()

        # Perform stock-in
        admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 10,
            "reason_category": "purchase",
            "warehouse_id": sample_material['warehouse_id']
        })

        # Stats should update
        updated = admin_client.get("/api/dashboard/stats").json()
        assert updated['today_in'] >= initial['today_in']

    def test_low_stock_triggered(self, admin_client, default_warehouse_id):
        """Creating a low-stock material should appear in alerts."""
        from database import get_db_connection
        import uuid

        sku = f"LOW-{uuid.uuid4().hex[:8].upper()}"
        name = f"Low Stock {sku}"

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, sku, 'Test', 5, 'pcs', 100, 'Z-01', default_warehouse_id))
        conn.commit()
        conn.close()

        resp = admin_client.get("/api/dashboard/low-stock-alert")
        assert resp.status_code == 200
        alerts = resp.json()
        # Our low-stock item should appear
        found = any(a['name'] == name for a in alerts)
        assert found, f"Low stock item '{name}' not found in alerts"


# ===========================================================================
# Multi-tenant scoping regression tests (added for SQLAlchemy migration safety
# net). These pin down the *current* tenant scoping behavior of dashboard
# endpoints so the migration cannot introduce silent cross-tenant leakage.
# ===========================================================================


@pytest.fixture()
def _reset_admin_tenant_after(test_db):
    """The tests below promote admin to global admin. Restore tenant_id=1 after
    each so we don't poison subsequent test files sharing the session DB."""
    yield
    import os as _os
    _os.environ['DATABASE_PATH'] = test_db
    import database as _database
    _database.DATABASE_PATH = test_db
    try:
        conn = _database.get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET tenant_id = 1 WHERE username = 'admin'")
        conn.commit()
        conn.close()
    except Exception:
        pass


def _seed_dashboard_setup(admin_client):
    """Promote admin to global, create two tenants with one warehouse each
    and seed materials + records (5 for A, 3 for B). Returns dict of ids."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()

    suffix = uuid.uuid4().hex[:6]
    t_a = admin_client.post("/api/tenants", json={
        "slug": f"da-{suffix}", "name": f"DA{suffix}"}).json()['id']
    t_b = admin_client.post("/api/tenants", json={
        "slug": f"db-{suffix}", "name": f"DB{suffix}"}).json()['id']
    wh_a = admin_client.post("/api/warehouses", json={
        "slug": f"dwa-{suffix}", "name": f"DWA{suffix}",
        "tenant_id": t_a}).json()['id']
    wh_b = admin_client.post("/api/warehouses", json={
        "slug": f"dwb-{suffix}", "name": f"DWB{suffix}",
        "tenant_id": t_b}).json()['id']

    # Seed materials + records directly
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, "
        "safe_stock, location, warehouse_id, tenant_id) "
        "VALUES (?, ?, 'CatA', 100, 'pcs', 1, '', ?, ?)",
        (f"DMA-{suffix}", f"sa-{suffix}", wh_a, t_a))
    mat_a = cur.lastrowid
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, "
        "safe_stock, location, warehouse_id, tenant_id) "
        "VALUES (?, ?, 'CatB', 200, 'pcs', 1, '', ?, ?)",
        (f"DMB-{suffix}", f"sb-{suffix}", wh_b, t_b))
    mat_b = cur.lastrowid

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for _ in range(5):
        cur.execute(
            "INSERT INTO inventory_records (material_id, type, quantity, "
            "operator, reason_category, warehouse_id, tenant_id, created_at)"
            " VALUES (?, 'in', 1, 'sys', 'purchase', ?, ?, ?)",
            (mat_a, wh_a, t_a, now))
    for _ in range(3):
        cur.execute(
            "INSERT INTO inventory_records (material_id, type, quantity, "
            "operator, reason_category, warehouse_id, tenant_id, created_at)"
            " VALUES (?, 'in', 1, 'sys', 'purchase', ?, ?, ?)",
            (mat_b, wh_b, t_b, now))
    conn.commit()
    conn.close()

    # Create tenant A admin user
    user_a = f"da-{suffix}"
    admin_client.post("/api/users", json={
        "username": user_a, "password": "Pass123!",
        "display_name": "DA", "role": "admin", "tenant_id": t_a,
    })

    return {
        "tenant_a": t_a, "tenant_b": t_b,
        "wh_a": wh_a, "wh_b": wh_b,
        "mat_a_name": f"DMA-{suffix}", "mat_b_name": f"DMB-{suffix}",
        "user_a": user_a, "password": "Pass123!",
    }


def _login_client(app_instance, username, password):
    from fastapi.testclient import TestClient
    c = TestClient(app_instance)
    r = c.post("/api/auth/login",
               json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


class TestDashboardTenantScoping:
    """Dashboard endpoints must scope to current_user.tenant_id."""

    def test_stats_scoped_to_tenant(self, admin_client, app_instance,
                                    monkeypatch, _reset_admin_tenant_after):
        monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
        ctx = _seed_dashboard_setup(admin_client)
        c_a = _login_client(app_instance, ctx['user_a'], ctx['password'])

        resp = c_a.get("/api/dashboard/stats")
        assert resp.status_code == 200
        data = resp.json()
        # Tenant A only seeded 1 material with quantity 100
        assert data['total_stock'] == 100
        assert data['material_types'] == 1
        # 5 in records today for A; tenant B's 3 must not leak
        assert data['today_in'] == 5

    def test_materials_all_scoped_to_tenant(self, admin_client, app_instance,
                                            monkeypatch,
                                            _reset_admin_tenant_after):
        monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
        ctx = _seed_dashboard_setup(admin_client)
        c_a = _login_client(app_instance, ctx['user_a'], ctx['password'])

        resp = c_a.get("/api/materials/all")
        assert resp.status_code == 200
        names = {m['name'] for m in resp.json()}
        assert ctx['mat_a_name'] in names
        assert ctx['mat_b_name'] not in names, (
            f"tenant A saw tenant B material: {names}")

    def test_category_distribution_scoped_to_tenant(
            self, admin_client, app_instance, monkeypatch,
            _reset_admin_tenant_after):
        monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
        ctx = _seed_dashboard_setup(admin_client)
        c_a = _login_client(app_instance, ctx['user_a'], ctx['password'])

        resp = c_a.get("/api/dashboard/category-distribution")
        assert resp.status_code == 200
        cats = {c['name'] for c in resp.json()}
        # Tenant B's category "CatB" should not leak through
        assert 'CatB' not in cats, f"tenant B category leaked: {cats}"

    def test_top_stock_scoped_to_tenant(self, admin_client, app_instance,
                                        monkeypatch,
                                        _reset_admin_tenant_after):
        monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
        ctx = _seed_dashboard_setup(admin_client)
        c_a = _login_client(app_instance, ctx['user_a'], ctx['password'])

        resp = c_a.get("/api/dashboard/top-stock")
        assert resp.status_code == 200
        names = resp.json()['names']
        assert ctx['mat_b_name'] not in names, (
            f"tenant A saw tenant B material in top-stock: {names}")

    def test_weekly_trend_scoped_to_tenant(self, admin_client, app_instance,
                                           monkeypatch,
                                           _reset_admin_tenant_after):
        monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
        ctx = _seed_dashboard_setup(admin_client)
        c_a = _login_client(app_instance, ctx['user_a'], ctx['password'])

        resp = c_a.get("/api/dashboard/weekly-trend")
        assert resp.status_code == 200
        data = resp.json()
        # Today's last bucket: tenant A had 5 in events, B had 3.
        # Tenant A view should see 5, never 8.
        today_in = data['in_data'][-1]
        assert today_in == 5, (
            f"weekly trend in_data leaked across tenants: {data}")


class TestDashboardGlobalAdmin:
    """Global admin (tenant_id=NULL) should see aggregated counts."""

    def test_global_admin_sees_aggregated(self, admin_client, app_instance,
                                          monkeypatch,
                                          _reset_admin_tenant_after):
        monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
        ctx = _seed_dashboard_setup(admin_client)
        # admin is currently global (NULL); the same admin_client's session
        # already authenticates as global admin since we promoted it.
        # Ensure it stays global by NOT logging in as anyone else.
        resp = admin_client.get("/api/dashboard/stats")
        assert resp.status_code == 200
        data = resp.json()
        # 5 (A) + 3 (B) = 8 today_in
        assert data['today_in'] >= 8, (
            f"global admin should see aggregated today_in >= 8: {data}")
