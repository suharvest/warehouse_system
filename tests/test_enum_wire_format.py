"""R3 wire-format regression net.

Asserts that role / record-type fields on key API responses are *string
literals* — not IntEnum values, not numbers, not enum repr strings. This
test must pass against pre-R3 code AND post-R3 code; if it breaks during
R3, that means the enum migration broke serialization.

Endpoints covered (per spec Section B):
    /api/auth/me                  -> role
    /api/users                    -> role per item
    /api/api-keys                 -> role per item (+ create flow)
    /api/mcp/connections          -> role per item (+ create flow)
    /api/inventory/records        -> type per item
"""
from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# /api/auth/me  -> role string
# ---------------------------------------------------------------------------

def test_auth_me_role_is_string_literal(admin_client):
    resp = admin_client.get("/api/auth/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    role = body["role"]
    assert isinstance(role, str), f"role must be str, got {type(role).__name__}: {role!r}"
    assert role == "admin", f"expected literal 'admin', got {role!r}"
    # Guard against accidental enum repr like 'RoleName.ADMIN'
    assert "." not in role
    assert role.islower()


# ---------------------------------------------------------------------------
# /api/users  -> role string per item
# ---------------------------------------------------------------------------

def test_users_list_roles_are_string_literals(admin_client):
    resp = admin_client.get("/api/users")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) >= 1
    for item in items:
        role = item["role"]
        assert isinstance(role, str), f"role must be str, got {type(role).__name__}"
        assert role in {"admin", "operate", "view"}, f"unexpected role wire value: {role!r}"


def test_users_create_round_trip_role_is_string(admin_client):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"r3_user_{suffix}",
        "password": "Pwd12345!",
        "display_name": "R3 Test",
        "role": "operate",
    }
    resp = admin_client.post("/api/users", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "operate"
    assert isinstance(body["role"], str)


# ---------------------------------------------------------------------------
# /api/api-keys  -> role string
# ---------------------------------------------------------------------------

def test_api_keys_create_and_list_roles_are_strings(admin_client, default_warehouse_id):
    name = f"r3-key-{uuid.uuid4().hex[:6]}"
    resp = admin_client.post("/api/api-keys", json={
        "name": name,
        "role": "view",
        "warehouse_id": default_warehouse_id,
    })
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["role"] == "view"
    assert isinstance(created["role"], str)

    resp = admin_client.get("/api/api-keys")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    found = [it for it in items if it["name"] == name]
    assert found, "newly-created api key not present in list"
    for it in items:
        assert isinstance(it["role"], str)
        assert it["role"] in {"admin", "operate", "view"}


# ---------------------------------------------------------------------------
# /api/mcp/connections  -> role string
# ---------------------------------------------------------------------------

def test_mcp_connections_role_is_string(admin_client, default_warehouse_id):
    # Create one (auto_start=False to avoid spawning a process)
    name = f"r3-mcp-{uuid.uuid4().hex[:6]}"
    resp = admin_client.post("/api/mcp/connections", json={
        "name": name,
        "mcp_endpoint": "wss://example.invalid/mcp",
        "role": "operate",
        "auto_start": False,
        "warehouse_id": default_warehouse_id,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    conn = body.get("connection") or {}
    assert conn.get("role") == "operate"
    assert isinstance(conn.get("role"), str)

    resp = admin_client.get("/api/mcp/connections")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert isinstance(items, list)
    for it in items:
        role_val = it.get("role")
        assert isinstance(role_val, str), f"mcp role wire format must be str: {role_val!r}"
        assert role_val in {"admin", "operate", "view"}


# ---------------------------------------------------------------------------
# /api/inventory/records  -> type string ('in' / 'out')
# ---------------------------------------------------------------------------

def test_inventory_records_type_is_string_literal(admin_client, sample_material, default_warehouse_id):
    # Create one IN and one OUT via stock APIs to populate records
    in_resp = admin_client.post("/api/materials/stock-in", json={
        "product_name": sample_material["name"],
        "quantity": 5,
        "reason_category": "purchase",
        "operator": "tester",
        "warehouse_id": default_warehouse_id,
    })
    assert in_resp.status_code == 200, in_resp.text

    out_resp = admin_client.post("/api/materials/stock-out", json={
        "product_name": sample_material["name"],
        "quantity": 3,
        "reason_category": "sell",
        "operator": "tester",
        "warehouse_id": default_warehouse_id,
    })
    assert out_resp.status_code == 200, out_resp.text

    resp = admin_client.get("/api/inventory/records", params={"page_size": 50})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"] if isinstance(body, dict) and "items" in body else body
    assert isinstance(items, list) and len(items) >= 2

    types_seen = set()
    for it in items:
        t = it["type"]
        assert isinstance(t, str), f"record type must be str, got {type(t).__name__}: {t!r}"
        assert t in {"in", "out"}, f"unexpected record type wire value: {t!r}"
        types_seen.add(t)
    assert {"in", "out"}.issubset(types_seen), f"expected both in/out present, got {types_seen}"


# ---------------------------------------------------------------------------
# Sanity: integer roles must NOT leak through anywhere (canary)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/auth/me",
    "/api/users",
    "/api/api-keys",
    "/api/mcp/connections",
])
def test_no_integer_role_leak(admin_client, path):
    resp = admin_client.get(path)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    items = payload if isinstance(payload, list) else [payload]
    for it in items:
        if isinstance(it, dict) and "role" in it:
            assert not isinstance(it["role"], int), (
                f"{path} leaked integer role {it['role']!r}; wire format must stay string"
            )
