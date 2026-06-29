"""Regression: whole-DB import / clear must invalidate the fuzzy-match index.

Background
----------
``/api/database/import`` and ``/api/database/clear`` bulk-mutate the materials
(and contacts) tables, but historically forgot to invalidate the in-memory
``FuzzyMatcher`` index that the running server keeps in ``app.state``. Because
``/api/materials/list`` defaults to ``fuzzy=true``, a code/name search after a
whole-DB import hit a *stale* index and returned an empty list — the imported
material was invisible until the process was restarted (which rebuilds the
index lazily). The Excel import path (``confirm_import_excel``) already
invalidated; these two paths did not.

These tests warm the SHARED app singleton matcher first (so the index is built
without the new row), then exercise the real HTTP endpoints, and assert the
post-mutation search reflects the change. Without the invalidate calls in
``import_database`` / ``clear_database`` they fail.
"""
import os
import sqlite3
import tempfile
import uuid
from io import BytesIO

import pytest


def _is_sqlite_backend() -> bool:
    url = os.environ.get('DATABASE_URL', '')
    return (not url) or url.startswith('sqlite')


# import_database / clear_database stream a literal .db file → sqlite-only.
sqlite_only = pytest.mark.skipif(
    not _is_sqlite_backend(),
    reason="sqlite-only feature (db import/clear operates on a literal .db file)",
)


def _make_import_db(name: str, sku: str) -> bytes:
    """Build a minimal SQLite .db (one material row) for /api/database/import."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(path)
        src.execute(
            """
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY,
                name TEXT,
                sku TEXT,
                category TEXT,
                quantity INTEGER,
                unit TEXT,
                warehouse_id INTEGER,
                tenant_id INTEGER
            )
            """
        )
        src.execute(
            "INSERT INTO materials (id, name, sku, category, quantity, unit, "
            "warehouse_id, tenant_id) VALUES (1, ?, ?, 'Imported', 7, 'pcs', 1, 1)",
            (name, sku),
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
def test_db_import_invalidates_fuzzy_index(admin_client):
    """After /api/database/import, a fuzzy=true search must find the new SKU.

    Reproduces the reported "用编号搜索搜不到" bug end-to-end through the same
    shared matcher the list endpoint uses.
    """
    sku = f"IMPREG-{uuid.uuid4().hex[:8].upper()}"
    name = f"导入回归物料 {sku}"

    # 1) Warm the SHARED app.state matcher: this builds the material partition
    #    from the *pre-import* DB (which does not contain `sku`) and leaves it
    #    clean — exactly the stale state that masked the imported row.
    warm = admin_client.get("/api/materials/list", params={"name": sku, "fuzzy": "true"})
    assert warm.status_code == 200
    assert warm.json()["total"] == 0, "precondition: SKU must not exist before import"

    # 2) Import a whole DB containing the new material.
    content = _make_import_db(name, sku)
    resp = admin_client.post(
        "/api/database/import",
        files={"file": ("regress-import.db", BytesIO(content), "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # 3) The very next fuzzy search must surface it (no restart, no manual
    #    invalidate). Fails if import_database forgets invalidate_cache().
    found = admin_client.get("/api/materials/list", params={"name": sku, "fuzzy": "true"})
    assert found.status_code == 200
    skus = [item["sku"] for item in found.json()["items"]]
    assert sku in skus, f"imported SKU {sku} not found via fuzzy search; index is stale"


@sqlite_only
def test_db_clear_invalidates_fuzzy_index(admin_client, app_instance):
    """After /api/database/clear, the fuzzy index must not retain dead entries.

    The list endpoint re-validates fuzzy hits against the DB (a JOIN drops
    deleted ids), so a stale index is invisible there. We therefore assert at
    the matcher level — the layer the agent's fuzzy `resolve` relies on.
    """
    from database import get_db_connection

    sku = f"CLRREG-{uuid.uuid4().hex[:8].upper()}"
    name = f"清空回归物料 {sku}"

    # Seed a material directly, then warm the shared matcher so the entry is in
    # the index before we clear.
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, "
        "location, warehouse_id, tenant_id) VALUES (?, ?, 'T', 0, 'pcs', 0, '', 1, 1)",
        (name, sku),
    )
    conn.commit()
    conn.close()

    # The shared singleton the HTTP endpoints mutate (get_fuzzy_matcher reads
    # app_instance.state). Rebuild it to include the freshly seeded row.
    from app import get_fuzzy_matcher
    matcher = get_fuzzy_matcher()
    matcher.invalidate_cache(entity_type="material")
    pre = matcher.search(sku, entity_type="material", tenant_id=1)
    assert any(r["extra"].get("sku") == sku for r in pre), "precondition: seeded SKU indexed"

    # Clear the whole DB via the real endpoint — must invalidate the index.
    resp = admin_client.post("/api/database/clear", json={"confirm": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # Without invalidate in clear_database, the matcher still returns the now
    # deleted SKU from its stale partition.
    post = matcher.search(sku, entity_type="material", tenant_id=1)
    assert not any(r["extra"].get("sku") == sku for r in post), (
        f"cleared SKU {sku} still present in fuzzy index; clear_database left it stale"
    )
