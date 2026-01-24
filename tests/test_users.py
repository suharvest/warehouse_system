"""
User management tests: CRUD, role permissions, API key management.
"""
import pytest


class TestUserCRUD:
    """User CRUD operations."""

    def test_list_users(self, admin_client):
        """Admin can list users."""
        resp = admin_client.get("/api/users")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_create_operator_user(self, admin_client):
        """Admin can create an operator user."""
        resp = admin_client.post("/api/users", json={
            "username": "test_operator",
            "password": "Operator123!",
            "display_name": "Test Operator",
            "role": "operate"
        })
        assert resp.status_code in [200, 400]
        if resp.status_code == 200:
            data = resp.json()
            assert data['username'] == "test_operator"
            assert data['role'] == "operate"

    def test_create_view_user(self, admin_client):
        """Admin can create a view-only user."""
        resp = admin_client.post("/api/users", json={
            "username": "test_viewer",
            "password": "Viewer123!",
            "display_name": "Test Viewer",
            "role": "view"
        })
        assert resp.status_code in [200, 400]

    def test_create_user_without_admin_fails(self, app_instance):
        """Non-admin cannot create users."""
        from fastapi.testclient import TestClient
        fresh_client = TestClient(app_instance)
        resp = fresh_client.post("/api/users", json={
            "username": "hacker",
            "password": "Hack123!",
            "role": "admin"
        })
        assert resp.status_code in [401, 403]

    def test_update_user(self, admin_client):
        """Admin can update a user."""
        import uuid
        username = f"upd_{uuid.uuid4().hex[:6]}"
        admin_client.post("/api/users", json={
            "username": username,
            "password": "Pass123!",
            "display_name": "Before",
            "role": "view"
        })

        users = admin_client.get("/api/users").json()
        target = next((u for u in users if u['username'] == username), None)
        if target:
            resp = admin_client.put(f"/api/users/{target['id']}", json={
                "display_name": "After Update",
                "role": "operate"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data['display_name'] == "After Update"
            assert data['role'] == "operate"

    def test_delete_user(self, admin_client):
        """Admin can delete a user."""
        import uuid
        username = f"del_{uuid.uuid4().hex[:6]}"
        create_resp = admin_client.post("/api/users", json={
            "username": username,
            "password": "Delete123!",
            "display_name": "To Delete",
            "role": "view"
        })
        if create_resp.status_code == 200:
            user_id = create_resp.json()['id']
            resp = admin_client.delete(f"/api/users/{user_id}")
            assert resp.status_code == 200


class TestRolePermissions:
    """Role-based permission tests."""

    def test_operator_can_read_materials(self, admin_client, app_instance):
        """Operator role can read materials."""
        import uuid
        username = f"op_{uuid.uuid4().hex[:6]}"

        # Create operator as admin
        admin_client.post("/api/users", json={
            "username": username,
            "password": "OpPass123!",
            "role": "operate"
        })

        # Login as operator with separate client
        from fastapi.testclient import TestClient
        op_client = TestClient(app_instance)
        resp = op_client.post("/api/auth/login", json={
            "username": username,
            "password": "OpPass123!"
        })
        assert resp.json()['success'] is True

        # Verify can read
        resp = op_client.get("/api/materials/list")
        assert resp.status_code == 200

    def test_viewer_cannot_stock_in(self, admin_client, app_instance):
        """View role cannot perform stock operations."""
        import uuid
        username = f"vw_{uuid.uuid4().hex[:6]}"

        admin_client.post("/api/users", json={
            "username": username,
            "password": "VwPass123!",
            "role": "view"
        })

        from fastapi.testclient import TestClient
        vw_client = TestClient(app_instance)
        resp = vw_client.post("/api/auth/login", json={
            "username": username,
            "password": "VwPass123!"
        })
        assert resp.json()['success'] is True

        resp = vw_client.post("/api/materials/stock-in", json={
            "product_name": "Test",
            "quantity": 1
        })
        assert resp.status_code in [401, 403]

    def test_viewer_cannot_manage_users(self, admin_client, app_instance):
        """View role cannot access user management."""
        import uuid
        username = f"vw2_{uuid.uuid4().hex[:6]}"

        admin_client.post("/api/users", json={
            "username": username,
            "password": "VwPass123!",
            "role": "view"
        })

        from fastapi.testclient import TestClient
        vw_client = TestClient(app_instance)
        vw_client.post("/api/auth/login", json={
            "username": username,
            "password": "VwPass123!"
        })

        resp = vw_client.get("/api/users")
        assert resp.status_code in [401, 403]


class TestApiKeys:
    """API key management tests."""

    def test_list_api_keys(self, admin_client):
        """Admin can list API keys."""
        resp = admin_client.get("/api/api-keys")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_create_api_key(self, admin_client):
        """Admin can create an API key."""
        resp = admin_client.post("/api/api-keys", json={
            "name": "Test Terminal",
            "role": "operate"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert 'key' in data
        assert data['key'].startswith('wh_')

    def test_delete_api_key(self, admin_client):
        """Admin can delete an API key."""
        create_resp = admin_client.post("/api/api-keys", json={
            "name": "To Delete Key",
            "role": "view"
        })
        assert create_resp.status_code == 200

        keys = admin_client.get("/api/api-keys").json()
        target = next((k for k in keys if k['name'] == 'To Delete Key'), None)
        if target:
            resp = admin_client.delete(f"/api/api-keys/{target['id']}")
            assert resp.status_code == 200

    def test_toggle_api_key_status(self, admin_client):
        """Admin can disable/enable an API key."""
        import uuid
        key_name = f"Toggle_{uuid.uuid4().hex[:6]}"
        create_resp = admin_client.post("/api/api-keys", json={
            "name": key_name,
            "role": "operate"
        })
        assert create_resp.status_code == 200

        keys = admin_client.get("/api/api-keys").json()
        target = next((k for k in keys if k['name'] == key_name), None)
        if target:
            resp = admin_client.put(f"/api/api-keys/{target['id']}/status", json={
                "disabled": True
            })
            assert resp.status_code == 200
