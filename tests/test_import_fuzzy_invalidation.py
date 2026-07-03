"""Regression: whole-DB import / clear must invalidate the fuzzy-match index.

Background
----------
``/api/database/import`` and ``/api/database/clear`` bulk-mutate the materials
(and contacts) tables, but historically forgot to invalidate the in-memory
``FuzzyMatcher`` kept in ``app.state``. Since ``/api/materials/list`` defaults to
``fuzzy=true``, a code/name search after a whole-DB import hit a *stale* index
and returned empty — the imported material stayed invisible until the process
restarted (lazy rebuild). The Excel import path already invalidated; these two
paths did not.

Isolation
---------
``/api/database/import`` clears + reimports a tenant's data, which is destructive
to the shared session sqlite DB. To avoid polluting other tests (e.g. wiping the
default tenant-1 warehouse), these tests run in ``multi_tenant`` mode against a
throwaway tenant — the same pattern as ``test_tenants.py`` — so only that
tenant's data is touched.
"""
import os
import sqlite3
import tempfile
import uuid
from io import BytesIO

import pytest

from test_tenants import (
    sqlite_only,
    _as_global_admin,
    _create_tenant_admin_with_warehouse,
    _login_as,
)


def _make_import_db(name: str, sku: str, unit: str) -> bytes:
    """Build a minimal SQLite .db (one material row) for /api/database/import."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(path)
        src.execute(
            "CREATE TABLE materials (id INTEGER PRIMARY KEY, name TEXT, sku TEXT, "
            "category TEXT, quantity INTEGER, unit TEXT, warehouse_id INTEGER, tenant_id INTEGER)"
        )
        src.execute(
            "INSERT INTO materials (id, name, sku, category, quantity, unit, warehouse_id, tenant_id) "
            "VALUES (1, ?, ?, 'Imported', 7, ?, 999, 999)",
            (name, sku, unit),
        )
        src.commit()
        src.close()
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@sqlite_only
def test_db_import_invalidates_fuzzy_index_and_sanitizes_unit(admin_client, app_instance, monkeypatch):
    """After /api/database/import, a fuzzy=true search must find the new SKU,
    and a formula-residue unit must be sanitized — exercised end-to-end through
    the shared matcher, isolated to a throwaway tenant.
    """
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)
    suffix = uuid.uuid4().hex[:8]
    tenant_id, _, username = _create_tenant_admin_with_warehouse(admin_client, suffix)
    tclient = _login_as(app_instance, username)

    sku = f"IMPREG-{suffix.upper()}"
    name = f"导入回归物料 {sku}"

    # 1) Warm the SHARED app.state matcher against the pre-import DB (no `sku` yet).
    warm = tclient.get("/api/materials/list", params={"name": sku, "fuzzy": "true"})
    assert warm.status_code == 200
    assert warm.json()["total"] == 0, "precondition: SKU must not exist before import"

    # 2) Import a whole DB with the new material (unit = leftover Excel formula).
    content = _make_import_db(name, sku, "=+VLOOKUP(C1,[1]x!$B:$I,8,0)")
    resp = tclient.post(
        "/api/database/import",
        files={"file": ("regress.db", BytesIO(content), "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # 3) The very next fuzzy search must surface it (no restart / manual invalidate).
    found = tclient.get("/api/materials/list", params={"name": sku, "fuzzy": "true"})
    assert found.status_code == 200
    items = found.json()["items"]
    hit = next((it for it in items if it["sku"] == sku), None)
    assert hit is not None, f"imported SKU {sku} not found via fuzzy search; index is stale"

    # 4) Formula-residue unit was sanitized, not copied verbatim.
    assert "=" not in (hit.get("unit") or ""), f"formula leaked into unit: {hit.get('unit')!r}"


def _fuzzy_skus(client, sku):
    """SKUs returned by /api/fuzzy-match for an exact-sku query (raw index, no DB
    re-validation — so stale entries surface here)."""
    r = client.get("/api/fuzzy-match", params={"q": sku, "entity_type": "material", "threshold": 50})
    assert r.status_code == 200, r.text
    return {c.get("extra", {}).get("sku") for c in r.json()["candidates"]}


@sqlite_only
def test_db_clear_invalidates_fuzzy_index(admin_client, app_instance, monkeypatch):
    """After /api/database/clear, the fuzzy index must not surface cleared rows.

    Asserted entirely through the client endpoints (/api/fuzzy-match, which serves
    raw index candidates without DB re-validation — the path the agent's resolve
    relies on). Going through the same client for both the clear and the lookup
    avoids the matcher-singleton identity hazard that bites direct
    ``from app import get_fuzzy_matcher`` access when other tests reload modules.
    Isolated to a throwaway tenant (clear deletes the current tenant's data).
    """
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _as_global_admin(admin_client)
    suffix = uuid.uuid4().hex[:8]
    _tenant_id, warehouse_id, username = _create_tenant_admin_with_warehouse(admin_client, suffix)
    tclient = _login_as(app_instance, username)

    sku = f"CLRREG-{suffix.upper()}"
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(['Name', 'SKU', 'Category', 'Quantity', 'Unit', 'Safe Stock', 'Location'])
    ws.append([f"清空回归物料 {sku}", sku, 'C', 5, 'pcs', 1, 'A-1'])
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    pv = tclient.post(
        "/api/materials/import-excel/preview",
        files={"file": ("s.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert pv.status_code == 200, pv.text
    cf = tclient.post("/api/materials/import-excel/confirm", json={
        "changes": pv.json()["preview"], "reason_category": "purchase",
        "confirm_new_skus": True, "confirm_disable_missing_skus": False,
        "warehouse_id": warehouse_id,
    })
    assert cf.status_code == 200, cf.text

    assert sku in _fuzzy_skus(tclient, sku), "precondition: seeded SKU indexed"

    resp = tclient.post("/api/database/clear", json={"confirm": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # Without invalidate in clear_database, the stale index still surfaces the SKU.
    assert sku not in _fuzzy_skus(tclient, sku), (
        f"cleared SKU {sku} still surfaced by /api/fuzzy-match; clear_database left index stale"
    )
