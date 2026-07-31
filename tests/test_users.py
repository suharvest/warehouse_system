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


class TestLastAdminGuard:
    """租户级不变量：至少保留一名启用的管理员。

    客户现场事故：新租户的唯一管理员把自己改成操作员后，再没人能改回来。
    """

    def _me(self, admin_client):
        return admin_client.get("/api/auth/me").json()

    def _fetch(self, admin_client, user_id):
        users = admin_client.get("/api/users").json()
        return next(u for u in users if u['id'] == user_id)

    def test_last_admin_cannot_self_demote(self, admin_client):
        me = self._me(admin_client)
        resp = admin_client.put(f"/api/users/{me['id']}", json={"role": "operate"})
        assert resp.status_code == 400
        assert "管理员" in resp.json()['error']
        assert self._fetch(admin_client, me['id'])['role'] == 'admin'

    def test_last_admin_cannot_self_disable(self, admin_client):
        me = self._me(admin_client)
        resp = admin_client.put(f"/api/users/{me['id']}", json={"is_disabled": True})
        assert resp.status_code == 400
        assert self._fetch(admin_client, me['id'])['is_disabled'] is False

    def test_last_admin_cannot_be_disabled_via_delete(self, admin_client):
        """DELETE = 禁用；自禁用先被 '不能禁用自己' 挡住，这里验证不变量本身
        对另一名管理员生效（同租户只剩一名启用管理员时不许再禁）。"""
        import uuid
        me = self._me(admin_client)
        created = admin_client.post("/api/users", json={
            "username": f"adm_{uuid.uuid4().hex[:6]}", "password": "Pass123!",
            "role": "admin",
        })
        assert created.status_code == 200
        second_id = created.json()['id']
        # 两名管理员在场：禁掉第二名是允许的
        assert admin_client.delete(f"/api/users/{second_id}").status_code == 200
        # 再把第一名（现在是唯一启用管理员）降级 → 拒绝
        resp = admin_client.put(f"/api/users/{me['id']}", json={"role": "view"})
        assert resp.status_code == 400

    def test_demote_second_admin_allowed(self, admin_client):
        import uuid
        created = admin_client.post("/api/users", json={
            "username": f"adm_{uuid.uuid4().hex[:6]}", "password": "Pass123!",
            "role": "admin",
        })
        assert created.status_code == 200
        second_id = created.json()['id']
        resp = admin_client.put(f"/api/users/{second_id}", json={"role": "operate"})
        assert resp.status_code == 200
        assert resp.json()['role'] == 'operate'
        admin_client.delete(f"/api/users/{second_id}")

    def test_non_admin_role_change_unaffected(self, admin_client):
        import uuid
        created = admin_client.post("/api/users", json={
            "username": f"plain_{uuid.uuid4().hex[:6]}", "password": "Pass123!",
            "role": "view",
        })
        assert created.status_code == 200
        uid = created.json()['id']
        resp = admin_client.put(f"/api/users/{uid}", json={"role": "operate"})
        assert resp.status_code == 200
        assert resp.json()['role'] == 'operate'
        admin_client.delete(f"/api/users/{uid}")
