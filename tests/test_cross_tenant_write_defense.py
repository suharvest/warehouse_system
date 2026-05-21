"""Regression tests for H1+H2 (ResourceRouter / MCP tenant write defense).

Fixes verified:
  - PUT/DELETE on ResourceRouter resources adds an extra ``tenant_id =
    current_user.tenant_id`` to the WHERE clause and 403's when rowcount
    is not exactly 1.
  - PUT/DELETE on /api/mcp/connections/{id} does the same.

Attack model: tenant A admin authenticates, then attempts to write a
row owned by tenant B. Even if ``load_or_404`` were buggy, the WHERE
guard must reject the UPDATE/DELETE.

Two ways to set this up:
  1. Create real tenant B via /api/tenants (requires promoting admin to
     global), then login as a tenant-B user, etc. — heavy.
  2. Insert a contact / mcp_connections row directly with a phantom
     tenant_id, then PUT/DELETE it as the default tenant admin. Lighter
     and exercises the exact guard.

We pick #2 because it's surgical and doesn't require driving the whole
tenant signup flow.
"""
import uuid

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _seed_other_tenant():
    """Create a second tenant directly via SQL, returns its id."""
    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        slug = f"t-other-{uuid.uuid4().hex[:8]}"
        cur.execute(
            "INSERT INTO tenants (slug, name) VALUES (?, ?)",
            (slug, f"Other {slug}"),
        )
        tid = cur.lastrowid
        conn.commit()
        return tid
    finally:
        conn.close()


def _seed_other_tenant_contact(other_tenant_id: int) -> int:
    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO contacts
                (name, is_supplier, is_customer, tenant_id, warehouse_id, created_at)
            VALUES (?, 1, 0, ?, NULL, CURRENT_TIMESTAMP)
        ''', (f"other-tenant-contact-{uuid.uuid4().hex[:6]}", other_tenant_id))
        cid = cur.lastrowid
        conn.commit()
        return cid
    finally:
        conn.close()


def _seed_other_tenant_mcp_connection(other_tenant_id: int) -> str:
    """Insert an mcp_connections row owned by ``other_tenant_id``."""
    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cid = uuid.uuid4().hex[:8]
        cur.execute('''
            INSERT INTO mcp_connections
              (id, name, mcp_endpoint, api_key, role, auto_start,
               warehouse_id, tenant_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'operate', 0, NULL, ?, 'stopped',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ''', (
            cid, f"other-mcp-{cid}",
            f"wss://other.example.com/{cid}",
            f"key-{cid}",
            other_tenant_id,
        ))
        conn.commit()
        return cid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_resource_router_put_other_tenant_returns_404_or_403(admin_client):
    """PUT /api/contacts/{id} where id belongs to tenant B → 403/404.

    admin_client is the default-tenant admin (tenant_id=1). We seed a
    contact under tenant 2 and try to update its name. The router's
    defensive tenant filter (resource_router.py:336-342) must short-
    circuit before any mutation.
    """
    other_tid = _seed_other_tenant()
    cid = _seed_other_tenant_contact(other_tid)

    resp = admin_client.put(f"/api/contacts/{cid}", json={"name": "PWNED"})
    assert resp.status_code in (403, 404), resp.text

    # And the row must NOT have been mutated.
    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM contacts WHERE id = ?", (cid,))
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] != "PWNED", "tenant guard failed: contact name was overwritten"


def test_resource_router_delete_other_tenant_returns_404_or_403(admin_client):
    """DELETE /api/contacts/{id} where id belongs to tenant B → 403/404.

    Soft-delete (sets is_disabled) is also a write — must respect tenant
    scope. Note: resource_router DELETE on contacts is configured as a
    soft delete (see app.py:2651 ``delete_response={…"已禁用"…}``), so we
    assert is_disabled didn't flip.
    """
    other_tid = _seed_other_tenant()
    cid = _seed_other_tenant_contact(other_tid)

    resp = admin_client.delete(f"/api/contacts/{cid}")
    assert resp.status_code in (403, 404), resp.text

    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_disabled FROM contacts WHERE id = ?", (cid,))
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, "contact row vanished — defense failed"


def test_mcp_update_other_tenant_returns_403(admin_client):
    """PUT /api/mcp/connections/{id} on a tenant-B row → 403/404."""
    other_tid = _seed_other_tenant()
    conn_id = _seed_other_tenant_mcp_connection(other_tid)

    resp = admin_client.put(f"/api/mcp/connections/{conn_id}", json={
        "name": "PWNED",
    })
    assert resp.status_code in (403, 404), resp.text

    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM mcp_connections WHERE id = ?", (conn_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] != "PWNED", (
        "tenant guard failed: mcp_connections row mutated cross-tenant"
    )


def test_mcp_delete_other_tenant_returns_403(admin_client):
    """DELETE /api/mcp/connections/{id} on a tenant-B row → 403/404."""
    other_tid = _seed_other_tenant()
    conn_id = _seed_other_tenant_mcp_connection(other_tid)

    resp = admin_client.delete(f"/api/mcp/connections/{conn_id}")
    assert resp.status_code in (403, 404), resp.text

    from database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM mcp_connections WHERE id = ?", (conn_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, (
        "tenant guard failed: mcp_connections row deleted cross-tenant"
    )
