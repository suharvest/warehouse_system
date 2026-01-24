"""
E2E test: Main page navigation and tab switching.
"""
import re
import pytest
from playwright.sync_api import Page, expect


class TestNavigation:
    """Main navigation tests."""

    def test_dashboard_tab_default(self, page: Page, server_url, setup_admin):
        """Dashboard tab should be active by default."""
        page.goto(server_url)
        dashboard_btn = page.locator('[data-tab="dashboard"]')
        expect(dashboard_btn).to_have_class(re.compile(r"active"))

    def test_switch_to_records_tab(self, page: Page, server_url, setup_admin):
        """Switching to records tab should show records content."""
        page.goto(server_url)
        page.click('[data-tab="records"]')
        page.wait_for_timeout(300)

        records_btn = page.locator('[data-tab="records"]')
        expect(records_btn).to_have_class(re.compile(r"active"))

    def test_switch_to_inventory_tab(self, page: Page, server_url, setup_admin):
        """Switching to inventory tab should show inventory content."""
        page.goto(server_url)
        page.click('[data-tab="inventory"]')
        page.wait_for_timeout(300)

        inventory_btn = page.locator('[data-tab="inventory"]')
        expect(inventory_btn).to_have_class(re.compile(r"active"))

    def test_switch_to_detail_tab(self, page: Page, server_url, setup_admin):
        """Switching to detail tab should work."""
        page.goto(server_url)
        page.click('[data-tab="detail"]')
        page.wait_for_timeout(300)

        detail_btn = page.locator('[data-tab="detail"]')
        expect(detail_btn).to_have_class(re.compile(r"active"))


class TestAdminNavigation:
    """Navigation tests for admin-only tabs."""

    def _login_as_admin(self, page: Page, server_url, credentials):
        """Helper to login as admin."""
        page.goto(server_url)
        page.click("#login-btn")
        page.fill("#login-username", credentials['username'])
        page.fill("#login-password", credentials['password'])
        page.click('[data-action="handleLogin"]')
        # Wait for login modal to close and admin UI to appear
        expect(page.locator("#login-modal")).not_to_be_visible()
        expect(page.locator('[data-tab="users"]')).to_be_visible()

    def test_contacts_tab_visible_after_login(self, page: Page, server_url, setup_admin):
        """Contacts tab should be visible for logged-in operator/admin."""
        self._login_as_admin(page, server_url, setup_admin)
        contacts_btn = page.locator('[data-tab="contacts"]')
        expect(contacts_btn).to_be_visible()

    def test_users_tab_visible_for_admin(self, page: Page, server_url, setup_admin):
        """Users management tab should be visible for admin."""
        self._login_as_admin(page, server_url, setup_admin)
        users_btn = page.locator('[data-tab="users"]')
        expect(users_btn).to_be_visible()

    def test_mcp_tab_visible_for_admin(self, page: Page, server_url, setup_admin):
        """MCP/Agent tab should be visible for admin."""
        self._login_as_admin(page, server_url, setup_admin)
        mcp_btn = page.locator('[data-tab="mcp"]')
        expect(mcp_btn).to_be_visible()

    def test_switch_to_users_tab(self, page: Page, server_url, setup_admin):
        """Admin can switch to users tab."""
        self._login_as_admin(page, server_url, setup_admin)
        page.click('[data-tab="users"]')
        page.wait_for_timeout(300)
        users_btn = page.locator('[data-tab="users"]')
        expect(users_btn).to_have_class(re.compile(r"active"))

    def test_switch_to_mcp_tab(self, page: Page, server_url, setup_admin):
        """Admin can switch to MCP tab."""
        self._login_as_admin(page, server_url, setup_admin)
        page.click('[data-tab="mcp"]')
        page.wait_for_timeout(300)
        mcp_btn = page.locator('[data-tab="mcp"]')
        expect(mcp_btn).to_have_class(re.compile(r"active"))


class TestGuestPermissions:
    """Verify guest cannot see admin tabs."""

    def test_contacts_hidden_for_guest(self, page: Page, server_url, setup_admin):
        """Contacts tab should be hidden for guests."""
        page.goto(server_url)
        contacts_btn = page.locator('[data-tab="contacts"]')
        expect(contacts_btn).not_to_be_visible()

    def test_users_hidden_for_guest(self, page: Page, server_url, setup_admin):
        """Users tab should be hidden for guests."""
        page.goto(server_url)
        users_btn = page.locator('[data-tab="users"]')
        expect(users_btn).not_to_be_visible()

    def test_mcp_hidden_for_guest(self, page: Page, server_url, setup_admin):
        """MCP tab should be hidden for guests."""
        page.goto(server_url)
        mcp_btn = page.locator('[data-tab="mcp"]')
        expect(mcp_btn).not_to_be_visible()
