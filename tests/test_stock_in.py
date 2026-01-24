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
            "reason": "Purchase"
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
            "reason": "Test batch creation"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert 'batch' in data
        assert data['batch']['batch_no'] is not None
        assert data['batch']['quantity'] == 30
        assert data['batch']['batch_id'] is not None

    def test_stock_in_zero_quantity_rejected(self, admin_client, sample_material):
        """Stock-in with zero quantity should be rejected."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 0,
            "reason": "Invalid"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_in_negative_quantity_rejected(self, admin_client, sample_material):
        """Stock-in with negative quantity should be rejected."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": -10,
            "reason": "Invalid"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_in_nonexistent_product(self, admin_client):
        """Stock-in for non-existent product should fail."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": "NonexistentProduct_XYZ",
            "quantity": 10,
            "reason": "Test"
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
            "reason": "Supplier delivery",
            "contact_id": contact_id
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['batch'] is not None

    def test_stock_in_updates_inventory(self, admin_client, sample_material):
        """Verify inventory is actually updated after stock-in."""
        # Get current quantity
        resp = admin_client.get("/api/materials/list")
        assert resp.status_code == 200
        materials = resp.json()['items']
        current = next((m for m in materials if m['name'] == sample_material['name']), None)
        assert current is not None
        old_qty = current['quantity']

        # Stock in
        admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 25,
            "reason": "Verify update"
        })

        # Check updated quantity
        resp = admin_client.get("/api/materials/list")
        materials = resp.json()['items']
        updated = next((m for m in materials if m['name'] == sample_material['name']), None)
        assert updated['quantity'] == old_qty + 25
