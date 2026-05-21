"""Regression tests for /api/auth/register (B3 — factory HTTP out of tx).

Fixes verified:
  - The route does a read-only ``SELECT … FROM tenants WHERE device_id = ?``
    BEFORE calling the factory API. If the device is already bound to a
    tenant, we 409 immediately and never touch the factory.
  - FACTORY_API_KEY missing → 503 fast (still before any DB write tx).
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _ensure_tenants_device_id_column(test_db):
    """Mirrors the fixture in test_reset_password.py — sqlite legacy init
    doesn't create tenants.device_id. ALTER it in here so register can run."""
    if os.environ.get('DATABASE_URL') and not os.environ['DATABASE_URL'].startswith('sqlite'):
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
    old = os.environ.get("DEPLOY_MODE")
    os.environ["DEPLOY_MODE"] = "multi_tenant"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("DEPLOY_MODE", None)
        else:
            os.environ["DEPLOY_MODE"] = old


def _seed_tenant_with_device(device_id: str):
    """Bind an existing tenant row to ``device_id`` directly via SQL."""
    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Update the default tenant — we just need the row to exist with the
        # device_id field set so the read-only precheck inside register sees it.
        cur.execute(
            "INSERT INTO tenants (slug, name, device_id) VALUES (?, ?, ?)",
            (f"t-bound-{device_id[:8]}", f"Bound {device_id[:8]}", device_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_register_tenant_existing_device_returns_409_fast(
    client, admin_client, _multi_tenant_mode, monkeypatch
):
    """Pre-bound device_id → 409 BEFORE factory API is called.

    We point ``FACTORY_API_BASE_URL`` at an obviously dead URL. If the
    handler reaches the httpx GET we'd see a 502/504, not a 409. So a
    clean 409 proves the device-bound short-circuit ran before the HTTP
    call.
    """
    import app as app_module
    device_id = "DEVICE-PREBOUND-001"
    _seed_tenant_with_device(device_id)

    # Make the factory call fail loudly if it ever runs.
    monkeypatch.setattr(app_module, "FACTORY_API_BASE_URL",
                        "http://127.0.0.1:1")  # closed port → ConnectionRefused
    monkeypatch.setattr(app_module, "FACTORY_API_KEY", "irrelevant-key")

    resp = client.post("/api/auth/register", json={
        "device_id": device_id,
        "username": "newadmin",
        "password": "Strong1Password!",
        "display_name": "new",
    })
    assert resp.status_code == 409, resp.text
    assert "已注册" in resp.text or "registered" in resp.text.lower()


def test_register_tenant_no_factory_key_returns_503(
    client, admin_client, _multi_tenant_mode, monkeypatch
):
    """FACTORY_API_KEY empty → 503 (fast, no HTTP call attempted)."""
    import app as app_module
    monkeypatch.setattr(app_module, "FACTORY_API_KEY", "")
    monkeypatch.setattr(app_module, "FACTORY_API_BASE_URL",
                        "http://127.0.0.1:1")

    resp = client.post("/api/auth/register", json={
        "device_id": "DEVICE-NEW-NEVER-SEEN",
        "username": "anotheradmin",
        "password": "Strong1Password!",
        "display_name": "x",
    })
    assert resp.status_code == 503, resp.text
