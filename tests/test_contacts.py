"""
Contact management tests: CRUD, supplier/customer filtering, disable.
"""
import pytest


class TestContactCRUD:
    """Contact CRUD operations."""

    def test_create_supplier(self, admin_client):
        """Create a supplier contact."""
        resp = admin_client.post("/api/contacts", json={
            "name": "Test Supplier A",
            "phone": "13800000001",
            "is_supplier": True,
            "is_customer": False
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['name'] == "Test Supplier A"
        assert data['is_supplier'] is True
        assert data['is_customer'] is False

    def test_create_customer(self, admin_client):
        """Create a customer contact."""
        resp = admin_client.post("/api/contacts", json={
            "name": "Test Customer B",
            "email": "customer@test.com",
            "is_supplier": False,
            "is_customer": True
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['is_customer'] is True
        assert data['is_supplier'] is False

    def test_create_dual_role_contact(self, admin_client):
        """Create a contact that is both supplier and customer."""
        resp = admin_client.post("/api/contacts", json={
            "name": "Dual Role Contact",
            "is_supplier": True,
            "is_customer": True
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['is_supplier'] is True
        assert data['is_customer'] is True

    def test_create_contact_no_type_rejected(self, admin_client):
        """Contact must be either supplier or customer."""
        resp = admin_client.post("/api/contacts", json={
            "name": "No Type Contact",
            "is_supplier": False,
            "is_customer": False
        })
        assert resp.status_code == 400

    def test_list_contacts(self, admin_client):
        """List all contacts with pagination."""
        resp = admin_client.get("/api/contacts")
        assert resp.status_code == 200
        data = resp.json()
        assert 'items' in data
        assert 'total' in data
        assert isinstance(data['items'], list)

    def test_get_contact_by_id(self, admin_client):
        """Get a specific contact by ID."""
        # Create one first
        create_resp = admin_client.post("/api/contacts", json={
            "name": "Get By ID Contact",
            "is_supplier": True,
            "is_customer": False
        })
        contact_id = create_resp.json()['id']

        resp = admin_client.get(f"/api/contacts/{contact_id}")
        assert resp.status_code == 200
        assert resp.json()['name'] == "Get By ID Contact"

    def test_update_contact(self, admin_client):
        """Update an existing contact."""
        # Create
        create_resp = admin_client.post("/api/contacts", json={
            "name": "Before Update",
            "is_supplier": True,
            "is_customer": False
        })
        contact_id = create_resp.json()['id']

        # Update
        resp = admin_client.put(f"/api/contacts/{contact_id}", json={
            "name": "After Update",
            "phone": "13900000001"
        })
        assert resp.status_code == 200
        assert resp.json()['name'] == "After Update"
        assert resp.json()['phone'] == "13900000001"

    def test_delete_contact(self, admin_client):
        """Delete (soft-disable) a contact."""
        # Create
        create_resp = admin_client.post("/api/contacts", json={
            "name": "To Delete",
            "is_supplier": True,
            "is_customer": False
        })
        contact_id = create_resp.json()['id']

        # Delete (soft-disable)
        resp = admin_client.delete(f"/api/contacts/{contact_id}")
        assert resp.status_code == 200

        # Verify disabled
        get_resp = admin_client.get(f"/api/contacts/{contact_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()['is_disabled'] is True


class TestContactFiltering:
    """Supplier/customer filtering tests."""

    def test_get_suppliers_list(self, admin_client):
        """Get only suppliers."""
        # Ensure at least one supplier exists
        admin_client.post("/api/contacts", json={
            "name": "Filter Supplier",
            "is_supplier": True,
            "is_customer": False
        })

        resp = admin_client.get("/api/contacts/suppliers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        for item in data:
            assert item['is_supplier'] is True

    def test_get_customers_list(self, admin_client):
        """Get only customers."""
        # Ensure at least one customer exists
        admin_client.post("/api/contacts", json={
            "name": "Filter Customer",
            "is_supplier": False,
            "is_customer": True
        })

        resp = admin_client.get("/api/contacts/customers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        for item in data:
            assert item['is_customer'] is True


class TestContactDisable:
    """Contact disable/enable tests."""

    def test_disable_contact(self, admin_client):
        """Disable a contact via update."""
        # Create
        create_resp = admin_client.post("/api/contacts", json={
            "name": "To Disable",
            "is_supplier": True,
            "is_customer": False
        })
        contact_id = create_resp.json()['id']

        # Disable
        resp = admin_client.put(f"/api/contacts/{contact_id}", json={
            "is_disabled": True
        })
        assert resp.status_code == 200
        assert resp.json()['is_disabled'] is True
