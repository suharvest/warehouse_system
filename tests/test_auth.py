"""
Authentication tests: setup, login, logout, session management, permissions.
"""
import pytest


class TestAuthSetup:
    """Initial setup and status checks."""

    def test_auth_status_returns_initialized(self, admin_client):
        """After setup, status should show initialized=True."""
        resp = admin_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data['initialized'] is True

    def test_duplicate_setup_rejected(self, admin_client):
        """Cannot setup admin twice."""
        resp = admin_client.post("/api/auth/setup", json={
            "username": "admin2",
            "password": "Password123!",
            "display_name": "Second Admin"
        })
        assert resp.status_code == 400


class TestLogin:
    """Login and session tests."""

    def test_login_success(self, admin_client):
        """Valid credentials should succeed."""
        resp = admin_client.post("/api/auth/login", json={
            "username": "admin",
            "password": "Admin123!"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['user']['username'] == 'admin'
        assert data['user']['role'] == 'admin'

    def test_login_wrong_password(self, app_instance, _admin_setup):
        """Wrong password should fail."""
        from fastapi.testclient import TestClient
        c = TestClient(app_instance)
        resp = c.post("/api/auth/login", json={
            "username": "admin",
            "password": "WrongPass!"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_login_nonexistent_user(self, app_instance, _admin_setup):
        """Nonexistent user should fail."""
        from fastapi.testclient import TestClient
        c = TestClient(app_instance)
        resp = c.post("/api/auth/login", json={
            "username": "nobody",
            "password": "whatever"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False


class TestSession:
    """Session and current user info."""

    def test_get_current_user(self, admin_client):
        """Logged-in user can get their own info."""
        resp = admin_client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data['username'] == 'admin'
        assert data['role'] == 'admin'

    def test_unauthenticated_cannot_access_me(self, app_instance):
        """Fresh client without session cannot access /me."""
        from fastapi.testclient import TestClient
        fresh_client = TestClient(app_instance)
        resp = fresh_client.get("/api/auth/me")
        assert resp.status_code == 401


class TestLogout:
    """Logout functionality."""

    def test_logout_clears_session(self, admin_client):
        """After logout, session should be cleared."""
        # Logout
        resp = admin_client.post("/api/auth/logout")
        assert resp.status_code == 200

        # After logout, /me should fail
        resp = admin_client.get("/api/auth/me")
        assert resp.status_code == 401


class TestPermissions:
    """Permission control tests."""

    def test_guest_can_read_dashboard(self, app_instance):
        """Guest users can read dashboard stats."""
        from fastapi.testclient import TestClient
        fresh_client = TestClient(app_instance)
        resp = fresh_client.get("/api/dashboard/stats")
        assert resp.status_code == 200

    def test_guest_can_read_materials(self, app_instance):
        """Guest users can read materials list."""
        from fastapi.testclient import TestClient
        fresh_client = TestClient(app_instance)
        resp = fresh_client.get("/api/materials/list")
        assert resp.status_code == 200

    def test_guest_cannot_stock_in(self, app_instance):
        """Guest cannot perform stock-in."""
        from fastapi.testclient import TestClient
        fresh_client = TestClient(app_instance)
        resp = fresh_client.post("/api/materials/stock-in", json={
            "product_name": "Test",
            "quantity": 10
        })
        assert resp.status_code in [401, 403]

    def test_guest_cannot_manage_users(self, app_instance):
        """Guest cannot access user management."""
        from fastapi.testclient import TestClient
        fresh_client = TestClient(app_instance)
        resp = fresh_client.get("/api/users")
        assert resp.status_code in [401, 403]
