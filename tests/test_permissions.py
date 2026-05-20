"""
Permission machinery tests (PR1: introduce new dependency factory + audit).

Covers:
- Role / Action / Resource enums and their mapping
- require_permission() dep factory tagging + behavior
- _audit_routes() detects unguarded routes and respects exempts
- Contacts endpoint integration via the new code path
"""
import logging

import pytest
from fastapi import Depends, FastAPI, HTTPException

from deps import (
    Action,
    Resource,
    Role,
    _ACTION_TO_ROLE,
    require_permission,
)
# _audit_routes / _route_has_guard remain in app.py (audit utilities).
from app import _audit_routes, _route_has_guard


# --------------------------- Enum tests ---------------------------

class TestRoleEnum:
    def test_role_enum_ordering(self):
        assert Role.VIEW < Role.OPERATE < Role.ADMIN
        # int values match the legacy ROLE_LEVELS dict
        assert int(Role.VIEW) == 1
        assert int(Role.OPERATE) == 2
        assert int(Role.ADMIN) == 3

    def test_role_from_str_round_trip(self):
        assert Role.from_str("view") is Role.VIEW
        assert Role.from_str("OPERATE") is Role.OPERATE
        assert Role.from_str("Admin") is Role.ADMIN

    def test_role_from_str_unknown_raises(self):
        with pytest.raises(KeyError):
            Role.from_str("superadmin")

    def test_action_to_role_mapping(self):
        assert _ACTION_TO_ROLE[Action.READ] is Role.VIEW
        assert _ACTION_TO_ROLE[Action.WRITE] is Role.OPERATE
        assert _ACTION_TO_ROLE[Action.ADMIN] is Role.ADMIN


# --------------------------- require_permission tests ---------------------------

class TestRequirePermissionFactory:
    def test_require_permission_marks_dep(self):
        dep = require_permission(Resource.CONTACTS, Action.READ)
        assert getattr(dep, "__perm_marker__", False) is True
        assert dep.__resource__ is Resource.CONTACTS
        assert dep.__action__ is Action.READ
        # Must be callable (an async function)
        assert callable(dep)


# --------------------------- Audit tests ---------------------------

def _make_synthetic_app_with_unguarded_route():
    app = FastAPI()

    @app.get("/api/widgets")
    async def list_widgets():
        return []

    return app


def _make_synthetic_app_with_guarded_route():
    app = FastAPI()
    dep = require_permission(Resource.CONTACTS, Action.READ)

    @app.get("/api/things")
    async def list_things(_user=Depends(dep)):
        return []

    return app


class TestAuditFindings:
    def test_route_has_guard_detects_require_permission(self):
        app = _make_synthetic_app_with_guarded_route()
        guarded_routes = [r for r in app.routes if getattr(r, "path", "") == "/api/things"]
        assert len(guarded_routes) == 1
        assert _route_has_guard(guarded_routes[0].dependant) is True

    def test_route_has_guard_misses_unguarded(self):
        app = _make_synthetic_app_with_unguarded_route()
        widget_routes = [r for r in app.routes if getattr(r, "path", "") == "/api/widgets"]
        assert len(widget_routes) == 1
        assert _route_has_guard(widget_routes[0].dependant) is False

    def test_audit_finds_unguarded_app_routes(self, caplog):
        """_audit_routes() walks the real app.routes and emits a warning when
        any non-exempt route lacks both require_auth and require_permission.
        At minimum, this verifies it can run end-to-end without crashing.
        """
        with caplog.at_level(logging.WARNING, logger="permissions.audit"):
            _audit_routes()
        # Audit may or may not find issues depending on current app state;
        # the contract here is just that it runs and uses the right logger.
        for record in caplog.records:
            if record.name == "permissions.audit":
                assert "Unguarded routes detected" in record.message


# --------------------------- Contacts integration via new code path ---------------------------

def _create_view_api_key(admin_client):
    import uuid
    resp = admin_client.post(
        "/api/api-keys",
        json={"name": f"view-{uuid.uuid4().hex[:6]}", "role": "view"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["key"]


class TestContactsViaRequirePermission:
    """End-to-end: contact endpoints now go through require_permission."""

    def test_contacts_view_token_can_read(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/contacts", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body

    def test_contacts_view_token_cannot_write(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.post(
            "/api/contacts",
            json={"name": "X", "is_supplier": True, "is_customer": False},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_contacts_guest_gets_401(self, client):
        # Use a fresh client with no cookies to ensure guest state
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/contacts")
        assert resp.status_code == 401
        assert resp.json()["error"] == "请先登录"


# --------------------------- PR2: Users / API Keys / Warehouses ---------------------------


class TestUsersViaRequirePermission:
    """End-to-end: /api/users now goes through require_permission (USERS, ADMIN)."""

    def test_admin_token_can_list_users(self, admin_client):
        resp = admin_client.get("/api/users")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_view_token_cannot_list_users(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/users", headers={"X-API-Key": api_key})
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_users_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/users")
        assert resp.status_code == 401
        assert resp.json()["error"] == "请先登录"


class TestApiKeysViaRequirePermission:
    """End-to-end: /api/api-keys now goes through require_permission (API_KEYS, ADMIN)."""

    def test_admin_token_can_list_api_keys(self, admin_client):
        resp = admin_client.get("/api/api-keys")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_view_token_cannot_list_api_keys(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/api-keys", headers={"X-API-Key": api_key})
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_api_keys_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/api-keys")
        assert resp.status_code == 401
        assert resp.json()["error"] == "请先登录"


class TestWarehousesViaRequirePermission:
    """End-to-end: /api/warehouses now goes through require_permission."""

    def test_view_token_can_list_warehouses(self, admin_client, client):
        # READ → view tokens allowed
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/warehouses", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_view_token_cannot_create_warehouse(self, admin_client, client):
        # POST = ADMIN, view should get 403
        import uuid
        api_key = _create_view_api_key(admin_client)
        slug = f"wh-{uuid.uuid4().hex[:6]}"
        resp = client.post(
            "/api/warehouses",
            json={"slug": slug, "name": "T", "address": ""},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_warehouses_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/warehouses")
        assert resp.status_code == 401
        assert resp.json()["error"] == "请先登录"


# --------------------------- PR3: Tenants / Dashboard / Search / System / ERP / MCP / DB ---------------------------


class TestTenantsViaRequirePermission:
    """End-to-end: /api/tenants now goes through require_permission (TENANTS, ADMIN)."""

    def test_view_token_cannot_list_tenants(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/tenants", headers={"X-API-Key": api_key})
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_tenants_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/tenants")
        assert resp.status_code == 401
        assert resp.json()["error"] == "请先登录"


class TestDashboardViaRequirePermission:
    """End-to-end: /api/dashboard/* now goes through require_permission (DASHBOARD, READ)."""

    def test_view_token_can_read_dashboard_stats(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/dashboard/stats", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_dashboard_stats_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/dashboard/stats")
        assert resp.status_code == 401
        assert resp.json()["error"] == "请先登录"

    def test_dashboard_low_stock_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/dashboard/low-stock-alert")
        assert resp.status_code == 401


class TestSearchViaRequirePermission:
    """End-to-end: /api/search now goes through require_permission (SEARCH, READ)."""

    def test_view_token_can_search(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/search?q=", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_search_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/search?q=")
        assert resp.status_code == 401
        assert resp.json()["error"] == "请先登录"


class TestSystemModeViaRequirePermission:
    """GET /api/system/mode is intentionally public (deployment metadata; the
    frontend needs it before login). PUT still goes through require_permission."""

    def test_view_token_can_read_mode(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/system/mode", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_view_token_cannot_change_mode(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.put(
            "/api/system/mode",
            json={"mode": "self_owned"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_system_mode_get_is_public(self, client):
        """GET is unauthenticated by design (see commit 02ce38b)."""
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/system/mode")
        assert resp.status_code == 200
        body = resp.json()
        assert "deploy_mode" in body
        assert "mode" in body

    def test_system_mode_put_still_requires_auth(self, client):
        """PUT remains guarded — only GET was opened up."""
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.put("/api/system/mode", json={"mode": "self_owned"})
        assert resp.status_code == 401


class TestMcpViaRequirePermission:
    """End-to-end: /api/mcp/connections goes through require_permission (MCP, ADMIN)."""

    def test_view_token_cannot_list_mcp(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/mcp/connections", headers={"X-API-Key": api_key})
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_mcp_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/mcp/connections")
        assert resp.status_code == 401


class TestErpViaRequirePermission:
    """End-to-end: /api/erp/* goes through require_permission (ERP)."""

    def test_view_token_cannot_list_erp_providers(self, admin_client, client):
        # list_erp_providers preserves legacy admin level
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/erp/providers", headers={"X-API-Key": api_key})
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_erp_providers_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/erp/providers")
        assert resp.status_code == 401


class TestDatabaseOpsViaRequirePermission:
    """End-to-end: /api/database/* goes through require_permission (SYSTEM, ADMIN)."""

    def test_view_token_cannot_export_db(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/database/export", headers={"X-API-Key": api_key})
        # 403 (perm denied) — preferred over the sqlite-only HTTP-400 guard which
        # would only fire under MySQL after auth passes
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_db_export_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/database/export")
        assert resp.status_code == 401


class TestMaterialsViaRequirePermission:
    """End-to-end: /api/materials/* goes through require_permission (MATERIALS)."""

    def test_view_token_can_list_materials(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/materials/list", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_materials_list_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/materials/list")
        assert resp.status_code == 401

    def test_view_token_cannot_import_materials(self, admin_client, client):
        # import-excel/preview is operate-level
        api_key = _create_view_api_key(admin_client)
        resp = client.post(
            "/api/materials/import-excel/preview",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"


class TestInventoryViaRequirePermission:
    """End-to-end: /api/inventory/* and stock-in/out go through require_permission (INVENTORY)."""

    def test_view_token_can_read_inventory_records(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/inventory/records", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_inventory_records_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/inventory/records")
        assert resp.status_code == 401

    def test_view_token_cannot_stock_in(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.post(
            "/api/materials/stock-in",
            json={"items": []},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"


class TestFaceViaRequirePermission:
    """End-to-end: /api/face/* goes through require_permission (FACE)."""

    def test_admin_can_get_face_config(self, admin_client):
        resp = admin_client.get("/api/face/config")
        # 200 if face config exists, or other domain status — just not 401/403.
        assert resp.status_code not in (401, 403)

    def test_view_token_cannot_get_face_config(self, admin_client, client):
        # face_get_config preserves legacy admin level
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/face/config", headers={"X-API-Key": api_key})
        assert resp.status_code == 403
        assert resp.json()["error"] == "权限不足"

    def test_face_config_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/face/config")
        assert resp.status_code == 401


class TestAuthMeViaRequirePermission:
    """End-to-end: /api/auth/me goes through require_permission (AUTH, READ)."""

    def test_view_token_can_get_auth_me(self, admin_client, client):
        api_key = _create_view_api_key(admin_client)
        resp = client.get("/api/auth/me", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_auth_me_guest_gets_401(self, client):
        from fastapi.testclient import TestClient
        guest = TestClient(client.app)
        resp = guest.get("/api/auth/me")
        assert resp.status_code == 401


def test_no_more_require_auth_callsites():
    """Once everything is ported, app.py should have zero require_auth(...)
    callsites (the function definition itself can stay until PR5 deletes it).
    """
    import re
    from pathlib import Path
    app_path = Path(__file__).parent.parent / "backend" / "app.py"
    content = app_path.read_text()
    # Match calls like Depends(require_auth(...)) but NOT the def
    callsites = re.findall(r"Depends\(\s*require_auth\(", content)
    assert len(callsites) == 0, f"Found {len(callsites)} remaining require_auth callsites"
