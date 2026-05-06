"""
Stock-in tests: normal stock-in, batch creation, inventory update, contact association.
"""
import pytest


class TestStockIn:
    """Stock-in operation tests."""

    def test_stock_in_success(self, admin_client, sample_material):
        """Normal stock-in should succeed and update inventory."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 50,
            "reason_category": "purchase",
            "warehouse_id": sample_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['product']['name'] == sample_material['name']
        assert data['product']['in_quantity'] == 50
        assert data['product']['old_quantity'] == 100
        assert data['product']['new_quantity'] == 150

    def test_stock_in_creates_batch(self, admin_client, sample_material):
        """Stock-in should automatically create a batch record."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 30,
            "reason_category": "purchase",
            "warehouse_id": sample_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert 'batch' in data
        assert data['batch']['batch_no'] is not None
        assert data['batch']['quantity'] == 30
        assert data['batch']['batch_id'] is not None

    def test_stock_in_uses_only_accessible_warehouse_when_omitted(self, admin_client, sample_material):
        """When the user has exactly one writable warehouse, warehouse_id may be omitted."""
        from database import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET tenant_id = 1 WHERE username = 'admin'")
        conn.commit()
        conn.close()

        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 10,
            "reason_category": "purchase"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['product']['new_quantity'] == 110

    def test_stock_in_zero_quantity_rejected(self, admin_client, sample_material):
        """Stock-in with zero quantity should be rejected."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 0,
            "reason_category": "purchase",
            "warehouse_id": sample_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_in_negative_quantity_rejected(self, admin_client, sample_material):
        """Stock-in with negative quantity should be rejected."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": -10,
            "reason_category": "purchase",
            "warehouse_id": sample_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_in_nonexistent_product(self, admin_client, default_warehouse_id):
        """Stock-in for non-existent product should fail."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": "NonexistentProduct_XYZ",
            "quantity": 10,
            "reason_category": "purchase",
            "warehouse_id": default_warehouse_id
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_in_with_contact(self, admin_client, sample_material):
        """Stock-in with supplier contact association."""
        # Create a supplier contact first
        contact_resp = admin_client.post("/api/contacts", json={
            "name": "StockIn Test Supplier",
            "is_supplier": True,
            "is_customer": False
        })
        assert contact_resp.status_code == 200
        contact_id = contact_resp.json()['id']

        # Stock-in with contact
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 20,
            "reason_category": "purchase",
            "contact_id": contact_id,
            "warehouse_id": sample_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['batch'] is not None

    def test_stock_in_updates_inventory(self, admin_client, sample_material):
        """Verify inventory is actually updated after stock-in."""
        # Stock in and verify via response
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 25,
            "reason_category": "purchase",
            "warehouse_id": sample_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['product']['new_quantity'] == data['product']['old_quantity'] + 25
