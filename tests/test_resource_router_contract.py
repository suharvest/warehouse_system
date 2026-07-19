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

# MySQL DATETIME has 1-second resolution by default (no fractional part), so
# three INSERTs in the same loop iteration share a ``created_at`` value and
# the ``ORDER BY created_at DESC`` tiebreak (insertion order) does not always
# reproduce the reverse-insertion order this assertion relies on. The contract
# itself (sort key + payload shape) is identical across backends; only the
# strict ordering check is flaky on MySQL.
_IS_MYSQL = bool(os.environ.get('DATABASE_URL', '')) and not os.environ.get(
    'DATABASE_URL', ''
).startswith('sqlite')


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
# List-contract helpers (in addition to shape-only goldens)
# ---------------------------------------------------------------------------
#
# The shape-only snapshots above only check the FIRST item's keys, so they
# miss real-world list-handler bugs: tenant filter inversion, sort drift,
# total-count off-by-one, include-disabled changes, pagination overlap. We
# layer the assertions below on top — the goldens stay byte-identical.


def _admin_tenant_id():
    """Look up the conftest admin's tenant_id (= 1 by default)."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT tenant_id FROM users WHERE username = 'admin'")
    row = cur.fetchone()
    conn.close()
    return row['tenant_id']


def _filter_by_tag(items, tag, key='name'):
    """Pick rows whose `key` contains the unique tag we seeded with."""
    return [it for it in items if tag in (it.get(key) or '')]


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

    # ------------------------------------------------------------------
    # Strong list-contract assertions (count / tenant / order / pagination)
    # ------------------------------------------------------------------
    tag = f"contractlist-{_suffix()}"
    expected_tenant = _admin_tenant_id()
    seeded_ids: list[int] = []
    # 11 records — pagination test below uses ``page_size=10`` (handler's
    # minimum) so 11 forces 2 pages with the second carrying the off-by-one
    # remainder. Names sorted by ``f"{tag}-{i:02d}"`` so the asc-name order
    # is known ahead of time.
    N = 11
    for i in range(N):
        rr = admin_client.post("/api/contacts", json={
            "name": f"{tag}-{i:02d}",
            "is_supplier": True, "is_customer": False,
        })
        assert rr.status_code == 200, rr.text
        seeded_ids.append(rr.json()["id"])

    # Total count: filter by ``name=tag`` so we don't depend on prior tests.
    r = admin_client.get(f"/api/contacts?name={tag}&page_size=100")
    assert r.status_code == 200
    body = r.json()
    items = body["items"]
    assert body["total"] == N, f"expected total={N} with tag filter, got {body!r}"
    assert len(items) == N

    # Tenant filter: every returned row must match admin's tenant_id (or
    # carry no tenant_id field, which the contacts shape does not — the
    # tenant_id column is filtered server-side via build_scope_predicates).
    # We assert via cross-tenant invisibility further down where applicable.
    # Order: contacts list sorts by name ASC. Names are
    # ``{tag}-00`` .. ``{tag}-06`` which sort lexicographically == seed order.
    returned_names = [it["name"] for it in items]
    assert returned_names == sorted(returned_names), (
        f"contacts list order drift: {returned_names!r}"
    )
    expected_names = [f"{tag}-{i:02d}" for i in range(N)]
    assert returned_names == expected_names, (
        f"contacts list expected {expected_names!r}, got {returned_names!r}"
    )

    # Pagination off-by-one: handler enforces page_size>=10; with N=11,
    # page_size=10 yields [10, 1] and pages 1+2 == seeded_ids, no overlap.
    seen: list[int] = []
    for page, expected_n in [(1, 10), (2, 1)]:
        rr = admin_client.get(
            f"/api/contacts?name={tag}&page={page}&page_size=10"
        )
        assert rr.status_code == 200
        bb = rr.json()
        assert bb["page"] == page
        assert bb["page_size"] == 10
        assert bb["total"] == N
        assert bb["total_pages"] == 2
        assert len(bb["items"]) == expected_n, (
            f"page {page}: expected {expected_n} items, got {len(bb['items'])}"
        )
        seen.extend(it["id"] for it in bb["items"])
    assert sorted(seen) == sorted(seeded_ids), (
        f"pagination overlap/gap: pages had {sorted(seen)!r} vs seeded "
        f"{sorted(seeded_ids)!r}"
    )
    assert len(seen) == len(set(seen)), "pagination returned duplicate IDs"

    # cleanup
    for cid_ in seeded_ids:
        admin_client.delete(f"/api/contacts/{cid_}")


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

    # ------------------------------------------------------------------
    # Strong list-contract assertions
    # ------------------------------------------------------------------
    tag = f"contractlist-{_suffix()}"
    expected_tenant = _admin_tenant_id()
    seeded_ids: list[int] = []
    for i in range(3):
        rr = admin_client.post("/api/warehouses", json={
            "slug": f"wh-{tag}-{i}",
            "name": f"WH {tag}-{i:02d}",
        })
        assert rr.status_code == 200, rr.text
        seeded_ids.append(rr.json()["id"])

    r = admin_client.get("/api/warehouses")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    mine = _filter_by_tag(body, tag)
    assert len(mine) == 3, f"expected 3 tagged warehouses, got {len(mine)}"

    # Tenant filter: every tagged row must match admin's tenant_id.
    for it in mine:
        assert it["tenant_id"] == expected_tenant, (
            f"cross-tenant leak: tagged warehouse {it!r}"
        )

    # Order: warehouses list sorts by ``is_default DESC, id ASC`` — within
    # our tag-set (none default), this collapses to id-asc == seed order.
    tagged_ids = [it["id"] for it in mine]
    assert tagged_ids == seeded_ids, (
        f"warehouses list order drift within tag: {tagged_ids!r} vs seeded "
        f"{seeded_ids!r}"
    )

    # cleanup
    for wid_ in seeded_ids:
        admin_client.delete(f"/api/warehouses/{wid_}")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _IS_MYSQL,
    reason="MySQL DATETIME(0) ties created_at across rapid INSERTs; "
           "strict reverse-insertion ordering is sqlite-only.",
)
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

    # ------------------------------------------------------------------
    # Strong list-contract assertions
    # ------------------------------------------------------------------
    tag = f"contractlist{_suffix()}"  # no dash — username regex is strict
    expected_tenant = _admin_tenant_id()
    seeded_ids: list[int] = []
    for i in range(3):
        rr = admin_client.post("/api/users", json={
            "username": f"u-{tag}-{i}",
            "password": "Pass1234!",
            "display_name": f"U {tag} {i:02d}",
            "role": "operate",
        })
        assert rr.status_code == 200, rr.text
        seeded_ids.append(rr.json()["id"])

    r = admin_client.get("/api/users")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    mine = _filter_by_tag(body, tag, key='username')
    assert len(mine) == 3, f"expected 3 tagged users, got {len(mine)}"

    for it in mine:
        assert it["tenant_id"] == expected_tenant, (
            f"cross-tenant leak: tagged user {it!r}"
        )

    # Order: users list sorts by ``created_at DESC`` — last-seeded first.
    tagged_ids = [it["id"] for it in mine]
    assert tagged_ids == list(reversed(seeded_ids)), (
        f"users list order drift: {tagged_ids!r} vs reversed seeded "
        f"{list(reversed(seeded_ids))!r}"
    )

    # cleanup
    for uid_ in seeded_ids:
        admin_client.delete(f"/api/users/{uid_}")


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _IS_MYSQL,
    reason="MySQL DATETIME(0) ties created_at across rapid INSERTs; "
           "strict reverse-insertion ordering is sqlite-only.",
)
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

    # ------------------------------------------------------------------
    # Strong list-contract assertions
    # ------------------------------------------------------------------
    tag = f"contractlist-{_suffix()}"
    seeded_ids: list[int] = []
    for i in range(3):
        rr = admin_client.post("/api/api-keys", json={
            "name": f"k-{tag}-{i:02d}",
            "role": "operate",
            "warehouse_id": default_warehouse_id,
        })
        assert rr.status_code == 200, rr.text
        seeded_ids.append(rr.json()["id"])

    r = admin_client.get("/api/api-keys")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    mine = _filter_by_tag(body, tag, key='name')
    assert len(mine) == 3, f"expected 3 tagged api-keys, got {len(mine)}"

    # Order: api-keys list sorts by ``created_at DESC`` — last-seeded first.
    tagged_ids = [it["id"] for it in mine]
    assert tagged_ids == list(reversed(seeded_ids)), (
        f"api-keys list order drift: {tagged_ids!r} vs reversed seeded "
        f"{list(reversed(seeded_ids))!r}"
    )

    # cleanup
    for kid_ in seeded_ids:
        admin_client.delete(f"/api/api-keys/{kid_}")


# ---------------------------------------------------------------------------
# MCP connections
# ---------------------------------------------------------------------------

def test_contract_mcp(admin_client, default_warehouse_id):
    s = _suffix()

    r = admin_client.post("/api/mcp/connections", json={
        "name": f"contract-mcp-{s}",
        "mcp_endpoint": f"http://127.0.0.1:9/mcp/{s}",
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

    # ------------------------------------------------------------------
    # Strong list-contract assertions
    # ------------------------------------------------------------------
    tag = f"contractlist-{_suffix()}"
    expected_tenant = _admin_tenant_id()
    seeded_ids: list[int] = []
    for i in range(3):
        rr = admin_client.post("/api/mcp/connections", json={
            "name": f"mcp-{tag}-{i:02d}",
            "mcp_endpoint": f"http://127.0.0.1:9/mcp/{tag}/{i}",
            "role": "operate",
            "auto_start": False,
            "warehouse_id": default_warehouse_id,
        })
        assert rr.status_code == 200, rr.text
        seeded_ids.append(rr.json()["connection"]["id"])

    r = admin_client.get("/api/mcp/connections")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    mine = _filter_by_tag(body, tag, key='name')
    assert len(mine) == 3, f"expected 3 tagged mcp connections, got {len(mine)}"

    for it in mine:
        assert it["tenant_id"] == expected_tenant, (
            f"cross-tenant leak: tagged mcp {it!r}"
        )

    # Order within tag-set: same tenant/warehouse, so falls to
    # ``created_at DESC`` — last-seeded first.
    tagged_ids = [it["id"] for it in mine]
    assert tagged_ids == list(reversed(seeded_ids)), (
        f"mcp list order drift: {tagged_ids!r} vs reversed seeded "
        f"{list(reversed(seeded_ids))!r}"
    )

    # cleanup
    for mid_ in seeded_ids:
        admin_client.delete(f"/api/mcp/connections/{mid_}")


# ---------------------------------------------------------------------------
# Face — rules
# ---------------------------------------------------------------------------

def test_contract_face_rules(admin_client, default_warehouse_id):
    s = _suffix()

    r = admin_client.post("/api/face/rules", json={
        "warehouse_id": default_warehouse_id,
        "operation": f"op-{s}",
        "require_face": True,
        "allowed_subject_ids": None,
        "min_confidence_override": None,
    })
    _check("face_rules/create", r.status_code, r.json())
    assert r.status_code == 200
    rid = r.json()["id"]

    r = admin_client.get("/api/face/rules")
    assert r.status_code == 200
    created_rule = next(item for item in r.json() if item["id"] == rid)
    _check("face_rules/list", r.status_code, [created_rule], shape_only=True)

    r = admin_client.put(f"/api/face/rules/{rid}", json={
        "warehouse_id": default_warehouse_id,
        "operation": f"op-{s}",
        "require_face": False,
        "allowed_subject_ids": None,
        "min_confidence_override": 0.8,
    })
    _check("face_rules/update", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.delete(f"/api/face/rules/{rid}")
    _check("face_rules/delete", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.put("/api/face/rules/999999999", json={
        "operation": "x", "require_face": False,
    })
    _check("face_rules/update_404", r.status_code, r.json())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Face — subjects
# ---------------------------------------------------------------------------

def test_contract_face_subjects(admin_client):
    s = _suffix()

    r = admin_client.post("/api/face/subjects", json={
        "name": f"Subject {s}",
        "employee_id": f"E{s}",
        "note": "n",
        "is_active": True,
    })
    _check("face_subjects/create", r.status_code, r.json())
    assert r.status_code == 200
    sid = r.json()["id"]

    r = admin_client.get("/api/face/subjects")
    _check("face_subjects/list", r.status_code, r.json(), shape_only=True)
    assert r.status_code == 200

    r = admin_client.put(f"/api/face/subjects/{sid}", json={
        "name": f"Subject {s} v2",
        "employee_id": f"E{s}",
        "note": "n",
        "is_active": True,
    })
    _check("face_subjects/update", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.delete(f"/api/face/subjects/{sid}")
    _check("face_subjects/delete", r.status_code, r.json())
    assert r.status_code == 200

    r = admin_client.put("/api/face/subjects/999999999", json={
        "name": "x", "is_active": True,
    })
    _check("face_subjects/update_404", r.status_code, r.json())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Face — enrollments (DELETE only — POST has async orchestrator + images)
# ---------------------------------------------------------------------------

def test_contract_face_enrollments_delete(admin_client):
    # 404 path is enough to lock DELETE wire shape; POST is hand-rolled
    # and exercised by tests/test_face.py.
    r = admin_client.delete("/api/face/enrollments/999999999")
    _check("face_enrollments/delete_404", r.status_code, r.json())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# ERP providers — GET / PUT / DELETE only (POST is multipart upload)
# ---------------------------------------------------------------------------

def test_contract_erp_providers(admin_client):
    # 404 paths lock the wire shape without needing to seed via multipart upload.
    r = admin_client.get("/api/erp/providers")
    _check("erp_providers/list", r.status_code, r.json(), shape_only=True)
    assert r.status_code == 200

    r = admin_client.get("/api/erp/providers/999999999")
    _check("erp_providers/get_404", r.status_code, r.json())
    assert r.status_code == 404

    r = admin_client.put("/api/erp/providers/999999999", json={
        "name": "x", "config": {},
    })
    _check("erp_providers/update_404", r.status_code, r.json())
    assert r.status_code == 404

    r = admin_client.delete("/api/erp/providers/999999999")
    _check("erp_providers/delete_404", r.status_code, r.json())
    assert r.status_code == 404
