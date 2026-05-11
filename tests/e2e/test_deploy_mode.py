"""
E2E tests for deploy_mode endpoint and the UI it drives.

Covers the recent fixes:
  - 02ce38b: /api/system/mode is public (no auth required) and exposes deploy_mode
  - 2c3e947: frontend single source of truth for deploy_mode
  - multi_tenant invariants: global admin gets "[全局管理]" prefix, tenant tab visible
"""
import re
import json
import urllib.request
import pytest
from playwright.sync_api import Page, expect


class TestSystemModeEndpoint:
    """The /api/system/mode endpoint must respond to anonymous callers."""

    def test_mode_endpoint_unauthenticated_single_tenant(self, server_url, setup_admin):
        """GET /api/system/mode returns 200 without any cookie/auth, body has deploy_mode."""
        req = urllib.request.Request(f"{server_url}/api/system/mode", method="GET")
        # Explicitly NO Cookie header — must work anonymous.
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
        assert "deploy_mode" in data
        assert data["deploy_mode"] == "single_tenant"

    def test_mode_endpoint_unauthenticated_multi_tenant(
        self, server_url_multi_tenant, setup_admin_multi_tenant
    ):
        """Multi-tenant server reports deploy_mode=multi_tenant publicly."""
        req = urllib.request.Request(
            f"{server_url_multi_tenant}/api/system/mode", method="GET"
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
        assert data["deploy_mode"] == "multi_tenant"


def _login(page: Page, base_url: str, username: str, password: str) -> None:
    page.goto(base_url)
    page.click("#login-btn")
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click('[data-action="handleLogin"]')
    expect(page.locator("#login-modal")).not_to_be_visible()


class TestMultiTenantUI:
    """Multi-tenant deployment: global admin sees [全局管理] + tenant tab."""

    def test_header_shows_global_admin_prefix(
        self, page: Page, server_url_multi_tenant, setup_admin_multi_tenant
    ):
        """After login as global admin, the user-name display shows '[全局管理]' prefix."""
        _login(
            page, server_url_multi_tenant,
            setup_admin_multi_tenant["username"], setup_admin_multi_tenant["password"],
        )
        name_display = page.locator("#user-name-display")
        expect(name_display).to_contain_text("全局管理")

    def test_tenants_sidebar_tab_visible(
        self, page: Page, server_url_multi_tenant, setup_admin_multi_tenant
    ):
        """Multi-tenant + global admin should see 租户管理 tab in sidebar."""
        _login(
            page, server_url_multi_tenant,
            setup_admin_multi_tenant["username"], setup_admin_multi_tenant["password"],
        )
        tenants_btn = page.locator('[data-tab="tenants"]')
        expect(tenants_btn).to_be_visible()

    def test_tenants_panel_no_single_tenant_message(
        self, page: Page, server_url_multi_tenant, setup_admin_multi_tenant
    ):
        """Clicking 租户管理 in multi_tenant must NOT show the '当前为单租户模式' empty state."""
        _login(
            page, server_url_multi_tenant,
            setup_admin_multi_tenant["username"], setup_admin_multi_tenant["password"],
        )
        page.click('[data-tab="tenants"]')
        page.wait_for_timeout(300)
        panel = page.locator("#tab-tenants")
        expect(panel).not_to_contain_text("当前为单租户模式")


class TestSingleTenantUI:
    """Default deployment: no [全局管理] prefix, tenant tab hidden."""

    def test_header_has_no_global_admin_prefix(
        self, page: Page, server_url, setup_admin
    ):
        """single_tenant admin display name must not contain '全局管理'."""
        _login(page, server_url, setup_admin["username"], setup_admin["password"])
        name_display = page.locator("#user-name-display")
        expect(name_display).not_to_contain_text("全局管理")

    def test_tenants_sidebar_tab_hidden(
        self, page: Page, server_url, setup_admin
    ):
        """single_tenant: 租户管理 tab must remain hidden even for admin."""
        _login(page, server_url, setup_admin["username"], setup_admin["password"])
        tenants_btn = page.locator('[data-tab="tenants"]')
        expect(tenants_btn).not_to_be_visible()
