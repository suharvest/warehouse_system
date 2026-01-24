"""
Dashboard tests: statistics, trends, alerts.
"""
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
            "reason": "Dashboard test"
        })

        # Stats should update
        updated = admin_client.get("/api/dashboard/stats").json()
        assert updated['today_in'] >= initial['today_in']

    def test_low_stock_triggered(self, admin_client):
        """Creating a low-stock material should appear in alerts."""
        from database import get_db_connection
        import uuid

        sku = f"LOW-{uuid.uuid4().hex[:8].upper()}"
        name = f"Low Stock {sku}"

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, sku, 'Test', 5, 'pcs', 100, 'Z-01'))
        conn.commit()
        conn.close()

        resp = admin_client.get("/api/dashboard/low-stock-alert")
        assert resp.status_code == 200
        alerts = resp.json()
        # Our low-stock item should appear
        found = any(a['name'] == name for a in alerts)
        assert found, f"Low stock item '{name}' not found in alerts"
