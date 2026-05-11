"""
E2E tests for empty-tenant guidance (commit abd4aee).

When deploy_mode=multi_tenant and 0 tenants exist, clicking '+ 添加仓库' as the
global admin should NOT open the warehouse modal — instead it toasts a hint
and switches to the 租户管理 tab.
"""
import re
import pytest
from playwright.sync_api import Page, expect


def _login_global_admin(page: Page, base_url: str, creds: dict) -> None:
    page.goto(base_url)
    page.click("#login-btn")
    page.fill("#login-username", creds["username"])
    page.fill("#login-password", creds["password"])
    page.click('[data-action="handleLogin"]')
    expect(page.locator("#login-modal")).not_to_be_visible()
    expect(page.locator('[data-tab="users"]')).to_be_visible()


def _open_warehouse_subtab(page: Page) -> None:
    page.click('[data-tab="users"]')
    page.wait_for_timeout(200)
    page.click('[data-action="switchSettingsSubTab"][data-sub-tab="warehouses"]')
    page.wait_for_timeout(200)
    expect(page.locator("#settings-panel-warehouses")).to_be_visible()


class TestEmptyTenantGuidance:
    def test_add_warehouse_with_no_tenant_shows_toast(
        self, page: Page, server_url_multi_tenant, setup_admin_multi_tenant
    ):
        """+ 添加仓库 with 0 tenants triggers the '请先创建租户' toast."""
        _login_global_admin(
            page, server_url_multi_tenant, setup_admin_multi_tenant
        )
        _open_warehouse_subtab(page)

        # Click the top-level "+ 添加仓库" button in the section header.
        page.click('#settings-panel-warehouses [data-action="showAddWarehouseModal"]')

        # A toast should appear containing the hint.
        toast = page.locator(".toast").filter(has_text=re.compile("请先创建租户"))
        expect(toast).to_be_visible()

    def test_add_warehouse_with_no_tenant_does_not_open_modal(
        self, page: Page, server_url_multi_tenant, setup_admin_multi_tenant
    ):
        """The warehouse modal must NOT open when 0 tenants exist."""
        _login_global_admin(
            page, server_url_multi_tenant, setup_admin_multi_tenant
        )
        _open_warehouse_subtab(page)

        page.click('#settings-panel-warehouses [data-action="showAddWarehouseModal"]')
        # Give the async tenant-list fetch + UI time to settle.
        page.wait_for_timeout(500)
        modal = page.locator("#warehouse-modal")
        # The .show class is what makes it visible; assert it's not present.
        expect(modal).not_to_have_class(re.compile(r"\bshow\b"))

    def test_add_warehouse_with_no_tenant_switches_to_tenants_tab(
        self, page: Page, server_url_multi_tenant, setup_admin_multi_tenant
    ):
        """After the toast, the active sidebar tab should be 租户管理."""
        _login_global_admin(
            page, server_url_multi_tenant, setup_admin_multi_tenant
        )
        _open_warehouse_subtab(page)

        page.click('#settings-panel-warehouses [data-action="showAddWarehouseModal"]')
        page.wait_for_timeout(500)
        tenants_btn = page.locator('[data-tab="tenants"]')
        expect(tenants_btn).to_have_class(re.compile(r"\bactive\b"))
