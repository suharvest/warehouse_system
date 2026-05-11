"""
E2E tests for first-time setup flow.

Fresh DB (no admin user) must trigger the setup modal on page load. Submitting
the form creates the admin and logs them in; reloading the page must NOT show
the setup modal again.
"""
import pytest
from playwright.sync_api import Page, expect


class TestFirstTimeSetup:
    def test_setup_modal_shown_on_fresh_db(self, page: Page, server_url_no_admin):
        """With no admin in DB, page load should display the setup modal."""
        page.goto(server_url_no_admin)
        setup_modal = page.locator("#setup-modal")
        expect(setup_modal).to_be_visible()

    def test_setup_form_creates_admin_and_logs_in(
        self, page: Page, server_url_no_admin
    ):
        """Filling and submitting the setup form should create the admin and log in."""
        page.goto(server_url_no_admin)
        expect(page.locator("#setup-modal")).to_be_visible()

        page.fill("#setup-username", "firstadmin")
        page.fill("#setup-display-name", "First Admin")
        page.fill("#setup-password", "Setup123!")
        page.fill("#setup-password-confirm", "Setup123!")
        page.click('[data-action="handleSetup"]')

        # Modal closes, admin-only nav becomes visible
        expect(page.locator("#setup-modal")).not_to_be_visible()
        expect(page.locator('[data-tab="users"]')).to_be_visible()

    def test_setup_persists_across_reload(self, page: Page, server_url_no_admin):
        """After completing setup, a page reload must NOT bring the setup modal back."""
        page.goto(server_url_no_admin)
        page.fill("#setup-username", "secondadmin")
        page.fill("#setup-display-name", "Second Admin")
        page.fill("#setup-password", "Setup123!")
        page.fill("#setup-password-confirm", "Setup123!")
        page.click('[data-action="handleSetup"]')
        expect(page.locator("#setup-modal")).not_to_be_visible()

        page.reload()
        # Setup modal must stay hidden — system is now initialized.
        expect(page.locator("#setup-modal")).not_to_be_visible()
        # Login button should also be hidden because the previous setup logged us in.
        # (If the session cookie didn't survive reload, login-btn is shown but
        # the setup-modal is still hidden — both are valid "initialized" states.)
