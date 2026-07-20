"""E2E coverage for an expired session while opening the MCP tab."""

import re

from playwright.sync_api import Page, Route, expect


def _login(page: Page, base_url: str, username: str, password: str) -> None:
    page.goto(base_url)
    page.click("#login-btn")
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click('[data-action="handleLogin"]')
    expect(page.locator("#login-modal")).not_to_be_visible()
    expect(page.locator('[data-tab="mcp"]')).to_be_visible()


def test_mcp_401_expires_session_and_stops_refresh(
    page: Page, server_url, setup_admin
) -> None:
    _login(
        page,
        server_url,
        setup_admin["username"],
        setup_admin["password"],
    )

    request_count = 0

    def reject_mcp_connections(route: Route) -> None:
        nonlocal request_count
        request_count += 1
        route.fulfill(
            status=401,
            content_type="application/json",
            body='{"error":"请先登录"}',
        )

    session_expired_alerts = []

    def accept_dialog(dialog) -> None:
        session_expired_alerts.append(dialog.message)
        dialog.accept()

    page.route("**/api/mcp/connections**", reject_mcp_connections)
    page.on("dialog", accept_dialog)
    page.click('[data-tab="mcp"]')

    expect(page.locator("#login-modal")).to_be_visible()
    expect(page.locator("#login-btn")).to_be_visible()
    expect(page.locator('[data-tab="dashboard"]')).to_have_class(
        re.compile(r"(^|\s)active(\s|$)")
    )
    expect(page.locator("#tab-dashboard")).to_have_class(
        re.compile(r"(^|\s)active(\s|$)")
    )
    expect(page.locator("#mcp-connections-tbody")).not_to_contain_text("HTTP 401")
    assert len(session_expired_alerts) == 1
    assert (
        "登录已过期" in session_expired_alerts[0]
        or "Session expired" in session_expired_alerts[0]
    )

    # The MCP refresh interval is 10 seconds. If switching back to the dashboard
    # did not stop it, the intercepted request count would increase here.
    page.wait_for_timeout(10_500)
    assert request_count == 1
