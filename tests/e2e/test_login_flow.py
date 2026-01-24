"""
E2E test: Login flow via Playwright.
"""
import pytest
from playwright.sync_api import Page, expect


class TestLoginFlow:
    """Login flow E2E tests."""

    def test_page_loads(self, page: Page, server_url, setup_admin):
        """Page should load with correct title."""
        page.goto(server_url)
        expect(page).to_have_title("智能仓管系统")

    def test_login_button_visible(self, page: Page, server_url, setup_admin):
        """Login button should be visible for unauthenticated users."""
        page.goto(server_url)
        login_btn = page.locator("#login-btn")
        expect(login_btn).to_be_visible()

    def test_login_modal_opens(self, page: Page, server_url, setup_admin):
        """Clicking login button should open the login modal."""
        page.goto(server_url)
        page.click("#login-btn")
        login_modal = page.locator("#login-modal")
        expect(login_modal).to_be_visible()

    def test_login_with_credentials(self, page: Page, server_url, setup_admin):
        """Complete login flow with valid credentials."""
        page.goto(server_url)
        page.click("#login-btn")

        # Fill in credentials
        page.fill("#login-username", setup_admin['username'])
        page.fill("#login-password", setup_admin['password'])

        # Submit
        page.click('[data-action="handleLogin"]')

        # Wait for modal to close and user info to appear
        page.wait_for_timeout(1000)
        login_modal = page.locator("#login-modal")
        expect(login_modal).not_to_be_visible()

    def test_login_with_wrong_password(self, page: Page, server_url, setup_admin):
        """Login with wrong password should show error."""
        page.goto(server_url)
        page.click("#login-btn")

        page.fill("#login-username", "admin")
        page.fill("#login-password", "WrongPassword!")

        page.click('[data-action="handleLogin"]')
        page.wait_for_timeout(500)

        # Login modal should still be visible (not closed)
        login_modal = page.locator("#login-modal")
        expect(login_modal).to_be_visible()

        # Error message should appear
        error_el = page.locator("#login-error")
        expect(error_el).to_be_visible()
