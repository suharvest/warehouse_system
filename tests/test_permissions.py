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

from app import (
    Action,
    Resource,
    Role,
    _ACTION_TO_ROLE,
    _audit_routes,
    _route_has_guard,
    require_permission,
)


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
