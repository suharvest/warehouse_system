"""
R2 contract tests — golden snapshots of CRUD JSON shapes.

Captures HTTP status + JSON response shape for list/get/create/update/delete
on every resource family that R2 (ResourceRouter) intends to migrate:
contacts (this dispatch), warehouses, users, api-keys, mcp.

The goldens are recorded against the un-refactored handlers; future R2
migrations must reproduce them byte-for-byte.

Usage:
    uv run pytest tests/test_resource_router_contract.py -v
    UPDATE_SNAPSHOTS=1 uv run pytest tests/test_resource_router_contract.py
        — regenerate goldens after intentional shape changes.

Snapshot format (per operation):
    {
      "status": <int>,
      "json": <scrubbed JSON>,         # volatile fields replaced with sentinels
      "json_keys": [..]                # sorted keys (object) or null (list/scalar)
    }

Volatile fields scrubbed: id / *_id, *_at (timestamps), plain_key / full_key,
created_by / created_at-style values; numeric ids are masked to ``"<int>"``.
The non-volatile keys (and their values) form the contract.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pytest


SNAPSHOT_DIR = Path(__file__).parent / "contracts"
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------

# Field names whose *values* are volatile (different per run) and must be
# masked before snapshotting. Keeping the *key* preserves the contract.
_VOLATILE_KEYS = {
    "id",
    "user_id",
    "tenant_id",
    "warehouse_id",
    "contact_id",
    "session_id",
    "key_id",
    "conn_id",
    "subject_id",
    "created_at",
    "updated_at",
    "expires_at",
    "last_used_at",
    "started_at",
    "stopped_at",
    "last_login_at",
    # secrets/random values
    "plain_key",
    "full_key",
    "key",
    "key_prefix",
    "secret",
    "session_token",
    # opaque service identifiers we don't lock to a specific value
    "process_id",
    "pid",
    # name-with-suffix fields use a UUID we generate per test, mask them too
    "name",
    "username",
    "display_name",
    "slug",
    "email",
    "phone",
    "address",
    "notes",
    "mcp_endpoint",
    # pagination counters depend on what other tests left in the session DB
    "total",
    "total_pages",
}

# Regexes for fully volatile string values (timestamps).
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def _mask_value(v: Any) -> Any:
    if isinstance(v, bool):
        return "<bool>"
    if isinstance(v, int):
        return "<int>"
    if isinstance(v, float):
        return "<float>"
    if isinstance(v, str):
        if _TS_RE.match(v):
            return "<ts>"
        return "<str>"
    if v is None:
        return None
    if isinstance(v, list):
        return [_mask_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _mask_value(val) for k, val in v.items()}
    return f"<{type(v).__name__}>"


def _scrub(obj: Any) -> Any:
    """Recursively mask volatile values; keep keys verbatim.

    Non-volatile scalars are kept as-is (they ARE the contract). Volatile
    scalars are replaced with type sentinels so the SHAPE is locked but
    irrelevant churn is hidden.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _VOLATILE_KEYS:
                # Single sentinel regardless of value type — the contract
                # is "this key exists", not "its value type happens to be X".
                # (None vs str varies based on what other tests left in
                # the DB, which is irrelevant to the wire contract.)
                out[k] = "<masked>"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(obj, list):
        # For list endpoints we only snapshot the FIRST item's shape; total
        # row counts depend on prior tests in the session.
        if obj and isinstance(obj[0], dict):
            return [_scrub(obj[0])]
        return [_scrub(x) for x in obj[:1]] if obj else []
    return obj


def _shape_only(obj: Any) -> Any:
    """Reduce ``obj`` to keys-only (any scalar value is a single sentinel).

    For LIST endpoints (or any endpoint where the actual values depend on
    pre-existing DB state from earlier tests), the contract is just the
    JSON shape: keys present. Scalars — incl. None — collapse to one
    sentinel because nullable fields can hold either a typed value or
    null depending on which row landed first.
    """
    if isinstance(obj, dict):
        return {k: _shape_only(v) for k, v in obj.items()}
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            return [_shape_only(obj[0])]
        return [_shape_only(x) for x in obj[:1]] if obj else []
    return "<any>"


def _snapshot(name: str, status: int, body: Any, *, shape_only: bool = False) -> dict:
    scrubbed = _shape_only(body) if shape_only else _scrub(body)
    if isinstance(scrubbed, dict):
        keys = sorted(scrubbed.keys())
    else:
        keys = None
    return {"status": status, "json": scrubbed, "json_keys": keys}


def _check(name: str, status: int, body: Any, *, shape_only: bool = False) -> None:
    """Compare against (or write) the snapshot file."""
    path = SNAPSHOT_DIR / f"{name}.json"
    actual = _snapshot(name, status, body, shape_only=shape_only)
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=2,
                                    sort_keys=True), encoding="utf-8")
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected, (
        f"\nContract drift for {name}:\n"
        f"expected: {json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True)}\n"
        f"actual:   {json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def test_contract_contacts(admin_client):
    s = _suffix()

    # CREATE
    r = admin_client.post("/api/contacts", json={
        "name": f"Contract Contact {s}",
        "phone": "13800000099",
        "address": "addr",
        "email": "c@example.com",
        "is_supplier": True,
        "is_customer": False,
        "notes": "n",
    })
    _check("contacts/create", r.status_code, r.json())
    assert r.status_code == 200
    cid = r.json()["id"]

    # GET
    r = admin_client.get(f"/api/contacts/{cid}")
    _check("contacts/get", r.status_code, r.json())
    assert r.status_code == 200

    # LIST (shape-only — values depend on prior tests in the session)
    r = admin_client.get("/api/contacts")
    _check("contacts/list", r.status_code, r.json(), shape_only=True)
    assert r.status_code == 200

    # UPDATE
    r = admin_client.put(f"/api/contacts/{cid}", json={
        "name": f"Contract Contact {s} v2",
        "phone": "13900000099",
    })
    _check("contacts/update", r.status_code, r.json())
    assert r.status_code == 200

    # DELETE (soft)
    r = admin_client.delete(f"/api/contacts/{cid}")
    _check("contacts/delete", r.status_code, r.json())
    assert r.status_code == 200

    # 404
    r = admin_client.get("/api/contacts/999999999")
    _check("contacts/get_404", r.status_code, r.json())
    assert r.status_code == 404
    assert r.json() == {"error": "联系方不存在"}

    # 400 (validation: must pick supplier or customer)
    r = admin_client.post("/api/contacts", json={
        "name": "no-type", "is_supplier": False, "is_customer": False,
    })
    _check("contacts/create_400", r.status_code, r.json())
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Warehouses
# ---------------------------------------------------------------------------

def test_contract_warehouses(admin_client):
    s = _suffix()

    r = admin_client.post("/api/warehouses", json={
        "slug": f"contract-wh-{s}",
        "name": f"Contract Warehouse {s}",
    })
    _check("warehouses/create", r.status_code, r.json())
    assert r.status_code == 200
    wid = r.json()["id"]

    r = admin_client.get("/api/warehouses")
    _check("warehouses/list", r.status_code, r.json(), shape_only=True)
    assert r.status_code == 200

    r = admin_client.put(f"/api/warehouses/{wid}", json={
        "name": f"Contract Warehouse {s} v2",
    })
    _check("warehouses/update", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.delete(f"/api/warehouses/{wid}")
    _check("warehouses/delete", r.status_code, r.json())
    # 200 on success per existing test_warehouses
    assert r.status_code == 200

    r = admin_client.put("/api/warehouses/999999999", json={"name": "x"})
    _check("warehouses/update_404", r.status_code, r.json())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def test_contract_users(admin_client):
    s = _suffix()

    r = admin_client.post("/api/users", json={
        "username": f"contract-u-{s}",
        "password": "Pass1234!",
        "display_name": f"Contract User {s}",
        "role": "operate",
    })
    _check("users/create", r.status_code, r.json())
    assert r.status_code == 200
    uid = r.json()["id"]

    r = admin_client.get("/api/users")
    _check("users/list", r.status_code, r.json(), shape_only=True)
    assert r.status_code == 200

    r = admin_client.put(f"/api/users/{uid}", json={
        "display_name": f"Contract User {s} v2",
    })
    _check("users/update", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.delete(f"/api/users/{uid}")
    _check("users/delete", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.put("/api/users/999999999", json={"display_name": "x"})
    _check("users/update_404", r.status_code, r.json())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

def test_contract_api_keys(admin_client, default_warehouse_id):
    s = _suffix()

    r = admin_client.post("/api/api-keys", json={
        "name": f"contract-key-{s}",
        "role": "operate",
        "warehouse_id": default_warehouse_id,
    })
    _check("api_keys/create", r.status_code, r.json())
    assert r.status_code == 200
    kid = r.json()["id"]

    r = admin_client.get("/api/api-keys")
    _check("api_keys/list", r.status_code, r.json(), shape_only=True)
    assert r.status_code == 200

    r = admin_client.delete(f"/api/api-keys/{kid}")
    _check("api_keys/delete", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.delete("/api/api-keys/999999999")
    _check("api_keys/delete_404", r.status_code, r.json())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# MCP connections
# ---------------------------------------------------------------------------

def test_contract_mcp(admin_client, default_warehouse_id):
    s = _suffix()

    r = admin_client.post("/api/mcp/connections", json={
        "name": f"contract-mcp-{s}",
        "mcp_endpoint": "http://127.0.0.1:9/mcp",
        "role": "operate",
        "auto_start": False,
        "warehouse_id": default_warehouse_id,
    })
    _check("mcp/create", r.status_code, r.json())
    assert r.status_code == 200
    mid = r.json()["connection"]["id"]

    r = admin_client.get("/api/mcp/connections")
    _check("mcp/list", r.status_code, r.json(), shape_only=True)
    assert r.status_code == 200

    r = admin_client.put(f"/api/mcp/connections/{mid}", json={
        "name": f"contract-mcp-{s}-v2",
    })
    _check("mcp/update", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.delete(f"/api/mcp/connections/{mid}")
    _check("mcp/delete", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.put("/api/mcp/connections/999999999",
                         json={"name": "x"})
    _check("mcp/update_404", r.status_code, r.json())
    assert r.status_code == 404
