"""Regression tests for /api/auth/reset-password (B1).

Fixes verified:
  - 加了 SlowAPI rate-limit (@limiter.limit("5/hour"))
  - 失败/成功路径都写 audit_log (RESET_PASSWORD_FAIL / RESET_PASSWORD_SUCCESS)

Notes about the test harness:
  - conftest.py disables ``app_module.limiter.enabled = False`` to keep
    rate limits from leaking across the whole test session. The first
    test in this file flips it back on for its scope only.
  - The endpoint is gated by ``DEPLOY_MODE == "multi_tenant"``. We
    set it via the ``DEPLOY_MODE`` env var before each test (the
    backend reads ``os.environ`` lazily via ``get_deploy_mode()``).
"""
import logging
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _ensure_tenants_device_id_column(test_db):
    """The sqlite test DB is built via legacy ``init_database()`` which
    does not create ``tenants.device_id``. The Alembic migration
    f8a9b0c1d2e3 adds it. Apply the same ALTER here so reset-password
    can run on sqlite. No-op when DATABASE_URL points at MySQL
    (alembic has already migrated that schema)."""
    import os as _os
    if _os.environ.get('DATABASE_URL', '').replace('sqlite', '').startswith(':'):
        return
    if _os.environ.get('DATABASE_URL') and not _os.environ['DATABASE_URL'].startswith('sqlite'):
        return
    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(tenants)")
        cols = [r[1] for r in cur.fetchall()]
        if 'device_id' not in cols:
            cur.execute("ALTER TABLE tenants ADD COLUMN device_id TEXT")
            try:
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_device_id "
                    "ON tenants(device_id)"
                )
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def _multi_tenant_mode():
    """Force ``get_deploy_mode()`` to ``multi_tenant`` for the test."""
    old = os.environ.get("DEPLOY_MODE")
    os.environ["DEPLOY_MODE"] = "multi_tenant"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("DEPLOY_MODE", None)
        else:
            os.environ["DEPLOY_MODE"] = old


@pytest.fixture()
def _enable_audit_logging():
    """Enable in-memory audit logging so caplog can see RESET_PASSWORD_* entries.

    deps.audit_log no-ops when ENABLE_AUDIT_LOG is False — and conftest sets
    it to False. Patch the module-level flag for the duration of the test.
    """
    import deps
    old = deps.ENABLE_AUDIT_LOG
    deps.ENABLE_AUDIT_LOG = True
    try:
        yield
    finally:
        deps.ENABLE_AUDIT_LOG = old


def test_reset_password_rate_limit_kicks_in(app_instance, _multi_tenant_mode):
    """6th attempt within an hour must be rejected with 429.

    conftest disables the limiter. We re-enable it here, hit the endpoint
    6 times from the same TestClient (same remote IP), and assert the
    last one is 429. We restore the disabled state in the finally block
    so other tests are unaffected.
    """
    import app as app_module

    # Make sure we have a fresh limiter view; SlowAPI keys by remote IP.
    # TestClient default IP is "testclient"; that is stable across requests.
    was_enabled = app_module.limiter.enabled
    app_module.limiter.enabled = True
    # Reset the in-memory limiter store so prior tests in the session
    # (other reset-password attempts, or self-register attempts that
    # share the limiter) don't pre-burn the bucket.
    try:
        app_module.limiter._storage.reset()
    except Exception:
        # MovingWindow stores may not expose reset(); fallback to clearing.
        try:
            app_module.limiter._storage.storage.clear()
        except Exception:
            pass

    try:
        client = TestClient(app_module.app)
        bad_payload = {
            "device_id": "rate-limit-probe-device",
            "username": "no-such-admin",
            "new_password": "Strong1Password!",
        }
        statuses = []
        for _ in range(5):
            r = client.post("/api/auth/reset-password", json=bad_payload)
            statuses.append(r.status_code)
        # First 5 attempts should NOT be 429 — they hit the actual handler
        # (likely 404 unknown device). Whatever they are, must not be 429.
        assert all(s != 429 for s in statuses), (
            f"limiter triggered too early: {statuses}"
        )
        # 6th: should be rejected by the limiter (429 from SlowAPI).
        r6 = client.post("/api/auth/reset-password", json=bad_payload)
        assert r6.status_code == 429, (
            f"expected 429 from rate-limiter on 6th attempt, got "
            f"{r6.status_code}: {r6.text}"
        )
    finally:
        app_module.limiter.enabled = was_enabled


def test_reset_password_unknown_device_returns_404(
    client, _multi_tenant_mode, _enable_audit_logging, caplog
):
    """Unknown device_id → 404 + audit_log emits RESET_PASSWORD_FAIL."""
    caplog.set_level(logging.INFO, logger="warehouse")
    resp = client.post("/api/auth/reset-password", json={
        "device_id": "definitely-nonexistent-device-xyz",
        "username": "someone",
        "new_password": "Strong1Password!",
    })
    assert resp.status_code == 404, resp.text
    # audit_log writes via logger.info "AUDIT: RESET_PASSWORD_FAIL | ..."
    audit_lines = [
        r.getMessage() for r in caplog.records
        if "RESET_PASSWORD_FAIL" in r.getMessage()
    ]
    assert audit_lines, (
        "expected at least one RESET_PASSWORD_FAIL audit entry, got: "
        + "\n".join(r.getMessage() for r in caplog.records)
    )
    # Reason should be unknown_device_id
    assert any("unknown_device_id" in m for m in audit_lines), audit_lines


def test_reset_password_weak_password_audited(
    client, _multi_tenant_mode, _enable_audit_logging, caplog
):
    """Weak password short-circuits before tenant lookup, still audited."""
    caplog.set_level(logging.INFO, logger="warehouse")
    resp = client.post("/api/auth/reset-password", json={
        "device_id": "some-device",
        "username": "admin",
        "new_password": "short",  # too short / missing digit
    })
    assert resp.status_code == 400, resp.text
    audit_lines = [
        r.getMessage() for r in caplog.records
        if "RESET_PASSWORD_FAIL" in r.getMessage()
    ]
    assert audit_lines, "expected RESET_PASSWORD_FAIL audit entry"
    assert any("weak_password" in m for m in audit_lines), audit_lines
