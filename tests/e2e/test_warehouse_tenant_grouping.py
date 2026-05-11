"""
E2E tests for tenant-grouped warehouse view (commit 7f5d6ef).

In multi_tenant deployments, the warehouse list shown to the global admin is
grouped by tenant with collapsible sections. Each section header offers an
inline '+ 添加仓库' button that opens the modal with the tenant pre-selected.
"""
import re
import pytest
from playwright.sync_api import Page, expect


def _login_and_open_warehouses(page: Page, fixture: dict) -> None:
    page.goto(fixture["url"])
    page.click("#login-btn")
    page.fill("#login-username", fixture["username"])
    page.fill("#login-password", fixture["password"])
    page.click('[data-action="handleLogin"]')
    expect(page.locator("#login-modal")).not_to_be_visible()
    expect(page.locator('[data-tab="users"]')).to_be_visible()

    page.click('[data-tab="users"]')
    page.wait_for_timeout(200)
    page.click('[data-action="switchSettingsSubTab"][data-sub-tab="warehouses"]')
    # Wait for the warehouses table to populate.
    expect(
        page.locator("#warehouses-tbody .warehouse-group-header").first
    ).to_be_visible(timeout=5000)


class TestTenantGrouping:
    def test_group_headers_visible(
        self, page: Page, server_url_multi_tenant_with_data
    ):
        """Two tenant group header rows must render."""
        _login_and_open_warehouses(page, server_url_multi_tenant_with_data)
        headers = page.locator("#warehouses-tbody .warehouse-group-header")
        expect(headers).to_have_count(2)

    def test_warehouse_rows_hidden_initially(
        self, page: Page, server_url_multi_tenant_with_data
    ):
        """Warehouse data rows start collapsed (display:none)."""
        _login_and_open_warehouses(page, server_url_multi_tenant_with_data)
        rows = page.locator("#warehouses-tbody tr.warehouse-row")
        expect(rows).to_have_count(2)
        # All warehouse rows should be hidden initially.
        for i in range(2):
            expect(rows.nth(i)).not_to_be_visible()

    def test_click_group_expands_its_rows_only(
        self, page: Page, server_url_multi_tenant_with_data
    ):
        """Clicking the first group header expands ONLY that group's rows."""
        _login_and_open_warehouses(page, server_url_multi_tenant_with_data)
        headers = page.locator("#warehouses-tbody .warehouse-group-header")
        first_header = headers.nth(0)
        second_header = headers.nth(1)
        first_tenant_id = first_header.get_attribute("data-tenant-id")
        second_tenant_id = second_header.get_attribute("data-tenant-id")

        first_header.click()
        # The clicked header should gain .expanded
        expect(first_header).to_have_class(re.compile(r"\bexpanded\b"))
        # Its warehouse rows become visible.
        first_row = page.locator(f".warehouse-row-t{first_tenant_id}").first
        expect(first_row).to_be_visible()
        # The other group remains collapsed.
        expect(second_header).not_to_have_class(re.compile(r"\bexpanded\b"))
        second_row = page.locator(f".warehouse-row-t{second_tenant_id}").first
        expect(second_row).not_to_be_visible()

    def test_inline_add_button_preselects_tenant(
        self, page: Page, server_url_multi_tenant_with_data
    ):
        """The '+ 添加仓库' button inside a group header opens the modal pre-selected."""
        _login_and_open_warehouses(page, server_url_multi_tenant_with_data)
        first_header = page.locator(
            "#warehouses-tbody .warehouse-group-header"
        ).nth(0)
        tenant_id = first_header.get_attribute("data-tenant-id")
        # Click the inline add button in this group header.
        first_header.locator(
            '[data-action="showAddWarehouseModal"]'
        ).click()
        modal = page.locator("#warehouse-modal")
        expect(modal).to_have_class(re.compile(r"\bshow\b"))
        # Tenant select group must be visible and pre-selected.
        group = page.locator("#warehouse-tenant-group")
        expect(group).to_be_visible()
        select = page.locator("#warehouse-tenant")
        # Wait for the async tenant list to populate before reading value.
        page.wait_for_function(
            "document.querySelector('#warehouse-tenant') && "
            "document.querySelector('#warehouse-tenant').options.length > 0 && "
            "!document.querySelector('#warehouse-tenant').disabled"
        )
        assert select.input_value() == tenant_id
