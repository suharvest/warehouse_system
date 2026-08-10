"""Tests for the startup auto-migration of pre-multi-tenant SQLite DBs.

Covers ``backend.app._recover_legacy_alembic_state`` (the ``users.tenant_id``
missing branch) and the shared implementation in
``backend.legacy_db_migration``.

The legacy fixture is built the honest way: create a DB at revision
``1826e23835b6`` via alembic, then *de-migrate* it back to the pre-multi-tenant
shape (drop ``tenant_id`` / ``warehouse_id``, drop ``alembic_version``, turn the
named UNIQUEs back into anonymous inline ones). Running the fixture forward
through ``alembic upgrade head`` therefore exercises the real chain, not a
hand-written approximation.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

INITIAL_SCHEMA_REVISION = "1826e23835b6"

# Tables whose tenant_id / warehouse_id columns exist at 1826e23835b6 and are
# stripped to synthesise the legacy shape.
_STRIP = {
    "users": ["tenant_id"],
    "materials": ["tenant_id", "warehouse_id"],
    "batches": ["tenant_id", "warehouse_id"],
    "inventory_records": ["tenant_id", "warehouse_id"],
    "contacts": ["tenant_id", "warehouse_id"],
    "mcp_connections": ["tenant_id", "warehouse_id"],
    "api_keys": ["tenant_id", "warehouse_id"],
    "erp_providers": ["tenant_id"],
}

# (table, column) pairs the legacy bootstrap declared as inline anonymous
# UNIQUE; later migrations drop them *by name*.
_ANONYMISE = [
    ("users", "username"),
    ("materials", "sku"),
    ("batches", "batch_no"),
    ("warehouses", "slug"),
    ("erp_providers", "provider_name"),
]

_COUNT_TABLES = ("users", "materials", "inventory_records", "contacts")


def _alembic_cfg() -> AlembicConfig:
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def _rebuild_legacy_shape(
    conn: sqlite3.Connection, table: str, drop: list[str], anon_unique: str | None
) -> None:
    """Rebuild ``table`` without ``drop`` columns and without named constraints.

    Emitted DDL mirrors the old hand-written bootstrap: plain column list, no
    named ``CONSTRAINT`` clauses, an inline anonymous ``UNIQUE`` on
    ``anon_unique``, and no foreign keys (SQLite's ``DROP COLUMN`` refuses to
    remove a column referenced by an FK, which is why we rebuild rather than
    ALTER).
    """
    info = list(conn.execute(f"PRAGMA table_info({table})"))
    keep = [r for r in info if r[1] not in drop]
    defs = []
    for _cid, name, ctype, notnull, dflt, pk in keep:
        piece = f'"{name}" {ctype or "TEXT"}'
        if pk:
            piece += " PRIMARY KEY"
            if (ctype or "").upper() == "INTEGER":
                piece += " AUTOINCREMENT"
        if name == anon_unique:
            piece += " UNIQUE"
        if notnull and not pk:
            piece += " NOT NULL"
        if dflt is not None:
            piece += f" DEFAULT {dflt}"
        defs.append(piece)

    tmp = f"{table}__legacy"
    col_list = ", ".join(f'"{r[1]}"' for r in keep)
    conn.execute(f'CREATE TABLE "{tmp}" ({", ".join(defs)})')
    conn.execute(f'INSERT INTO "{tmp}" ({col_list}) SELECT {col_list} FROM "{table}"')
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{table}"')


def _make_legacy_db(path: Path) -> dict[str, int]:
    """Create a pre-multi-tenant DB at ``path``; return per-table row counts."""
    prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    try:
        alembic_command.upgrade(_alembic_cfg(), INITIAL_SCHEMA_REVISION)
    finally:
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        # Drop indexes that reference the columns we're about to remove.
        for name, tbl in conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index' "
            "AND name NOT LIKE 'sqlite_autoindex%'"
        ).fetchall():
            if tbl in _STRIP:
                conn.execute(f'DROP INDEX "{name}"')

        anon = dict(_ANONYMISE)
        for table in sorted(set(_STRIP) | set(anon)):
            _rebuild_legacy_shape(
                conn, table, _STRIP.get(table, []), anon.get(table)
            )

        # Seed a little business data so we can assert nothing is lost.
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role) "
            "VALUES (1, 'legacy_admin', 'x', 'admin')"
        )
        for i in range(1, 4):
            conn.execute(
                "INSERT INTO materials (id, name, sku, category, quantity, unit) "
                "VALUES (?, ?, ?, '默认', ?, '个')",
                (i, f"物料{i}", f"SKU-{i}", i * 10),
            )
        for i in range(1, 6):
            conn.execute(
                "INSERT INTO inventory_records "
                "(material_id, type, quantity, operator) VALUES (1, 'in', ?, 'op')",
                (i,),
            )
        conn.execute(
            "INSERT INTO contacts (id, name, is_supplier) VALUES (1, '供应商A', 1)"
        )
        conn.execute("DROP TABLE IF EXISTS alembic_version")
        conn.commit()
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in _COUNT_TABLES
        }
    finally:
        conn.close()
    return counts


def _cols(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in _COUNT_TABLES
        }
    finally:
        conn.close()


def _backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.bak.legacy_migrate_*"))


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    """A pre-multi-tenant SQLite DB wired up as the process DB."""
    path = tmp_path / "warehouse.db"
    counts = _make_legacy_db(path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DEPLOY_MODE", "single_tenant")
    monkeypatch.delenv("AUTO_MIGRATE_LEGACY_DB", raising=False)
    import db as db_module

    db_module.reset_engine()
    yield path, counts
    db_module.reset_engine()


def _recover(path: Path):
    """Call the startup hook's legacy-recovery step against ``path``."""
    import app as app_module

    app_module._recover_legacy_alembic_state(_alembic_cfg())
    return app_module


# --------------------------------------------------------------------------
# 1. legacy DB + single_tenant -> auto-migrates and reaches head
# --------------------------------------------------------------------------
def test_legacy_db_single_tenant_auto_migrates_to_head(legacy_db):
    path, before = legacy_db
    assert "tenant_id" not in _cols(path, "users")

    _recover(path)

    assert "tenant_id" in _cols(path, "users")
    backups = _backups(path)
    assert len(backups) == 1, "exactly one pre-migration backup expected"
    assert backups[0].stat().st_size > 0

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        stamped = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    assert stamped[0] == INITIAL_SCHEMA_REVISION

    # The whole remaining chain must apply cleanly.
    alembic_command.upgrade(_alembic_cfg(), "head")

    after = _counts(path)
    assert after == before, f"row counts changed: {before} -> {after}"
    assert "tenant_id" in _cols(path, "materials")
    assert "warehouse_id" in _cols(path, "materials")


def test_legacy_db_single_tenant_app_starts_and_health_is_200(tmp_path):
    """End-to-end: real app startup on a legacy DB, then ``GET /health``.

    Run out-of-process so the app import / alembic run cannot leak module
    state into the rest of the pytest session.
    """
    path = tmp_path / "warehouse.db"
    _make_legacy_db(path)

    script = textwrap.dedent(
        f"""
        import os, sys
        os.environ["DATABASE_PATH"] = {str(path)!r}
        os.environ["DATABASE_URL"] = "sqlite:///" + {str(path)!r}
        os.environ["DEPLOY_MODE"] = "single_tenant"
        os.environ["INIT_MOCK_DATA"] = "false"
        sys.path.insert(0, {str(BACKEND_DIR)!r})
        os.chdir({str(BACKEND_DIR)!r})
        from fastapi.testclient import TestClient
        from app import app
        with TestClient(app) as c:
            r = c.get("/health")
            print("HEALTH", r.status_code, r.text)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "HEALTH 200" in proc.stdout, proc.stdout + proc.stderr
    assert len(_backups(path)) == 1


# --------------------------------------------------------------------------
# 2. legacy DB + DEPLOY_MODE != single_tenant -> still refuses to start
# --------------------------------------------------------------------------
def test_legacy_db_multi_tenant_refuses_to_start(legacy_db, monkeypatch):
    path, _ = legacy_db
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")

    with pytest.raises(RuntimeError) as exc:
        _recover(path)

    msg = str(exc.value)
    assert "users.tenant_id is missing" in msg
    assert "DEPLOY_MODE" in msg
    assert "-m backend.legacy_db_migration" in msg
    assert "tenant_id" not in _cols(path, "users")
    assert _backups(path) == []


# --------------------------------------------------------------------------
# 3. legacy DB + opt-out -> still refuses to start
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_legacy_db_opt_out_refuses_to_start(legacy_db, monkeypatch, value):
    path, _ = legacy_db
    monkeypatch.setenv("AUTO_MIGRATE_LEGACY_DB", value)

    with pytest.raises(RuntimeError) as exc:
        _recover(path)

    assert "AUTO_MIGRATE_LEGACY_DB is disabled" in str(exc.value)
    assert "tenant_id" not in _cols(path, "users")
    assert _backups(path) == []


def test_opt_out_default_is_on(monkeypatch):
    import app as app_module

    monkeypatch.delenv("AUTO_MIGRATE_LEGACY_DB", raising=False)
    assert app_module._auto_migrate_legacy_enabled() is True
    monkeypatch.setenv("AUTO_MIGRATE_LEGACY_DB", "")
    assert app_module._auto_migrate_legacy_enabled() is True
    monkeypatch.setenv("AUTO_MIGRATE_LEGACY_DB", "1")
    assert app_module._auto_migrate_legacy_enabled() is True


# --------------------------------------------------------------------------
# 4. already-migrated DB -> idempotent no-op
# --------------------------------------------------------------------------
def test_already_migrated_db_is_noop(legacy_db):
    path, before = legacy_db
    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")
    first_backups = _backups(path)
    assert len(first_backups) == 1
    cols_after_first = _cols(path, "users")

    # Second startup: alembic_version is populated -> recovery is a no-op.
    _recover(path)

    assert _backups(path) == first_backups, "no second backup should be taken"
    assert _cols(path, "users") == cols_after_first
    assert _counts(path) == before


def test_migrate_module_is_idempotent(legacy_db):
    """Calling ``migrate`` twice reports no schema changes the second time."""
    path, _ = legacy_db
    import legacy_db_migration as mod

    first = mod.migrate(path, log=lambda _m: None)
    assert first.changed
    assert first.backup_path.exists()

    second = mod.migrate(path, log=lambda _m: None)
    assert second.changes == [], f"unexpected second-pass changes: {second.changes}"
    assert not second.changed


def test_needs_legacy_migration_detection(legacy_db):
    path, _ = legacy_db
    import legacy_db_migration as mod

    assert mod.needs_legacy_migration(path) is True
    mod.migrate(path, log=lambda _m: None)
    assert mod.needs_legacy_migration(path) is False


# --------------------------------------------------------------------------
# 5. fresh empty DB -> untouched, alembic builds from scratch
# --------------------------------------------------------------------------
def test_fresh_empty_db_is_unaffected(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    sqlite3.connect(str(path)).close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DEPLOY_MODE", "single_tenant")
    import db as db_module

    db_module.reset_engine()
    try:
        _recover(path)
        assert _backups(path) == []

        alembic_command.upgrade(_alembic_cfg(), "head")
        assert "tenant_id" in _cols(path, "users")
        assert _counts(path)["users"] == 0
    finally:
        db_module.reset_engine()


# --------------------------------------------------------------------------
# 6. warehouse_id is backfilled -- migrated rows stay visible to scoped queries
# --------------------------------------------------------------------------
_WAREHOUSE_SCOPED = ("materials", "batches", "inventory_records", "contacts")


def _default_wh_id(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT id FROM warehouses ORDER BY is_default DESC, id ASC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()


def _null_warehouse_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            t: conn.execute(
                f"SELECT COUNT(*) FROM {t} WHERE warehouse_id IS NULL"
            ).fetchone()[0]
            for t in _WAREHOUSE_SCOPED
        }
    finally:
        conn.close()


def _distinct_warehouse_ids(path: Path, table: str) -> list:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return sorted(
            r[0] for r in conn.execute(f"SELECT DISTINCT warehouse_id FROM {table}")
        )
    finally:
        conn.close()


def test_warehouse_id_is_backfilled_not_left_null(legacy_db):
    """The bug: rows arrive with warehouse_id NULL and vanish from the UI."""
    path, _ = legacy_db
    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")

    wh = _default_wh_id(path)
    assert _null_warehouse_counts(path) == {t: 0 for t in _WAREHOUSE_SCOPED}
    for table in _WAREHOUSE_SCOPED:
        ids = _distinct_warehouse_ids(path, table)
        assert ids in ([], [wh]), f"{table} has unexpected warehouse ids {ids}"


def test_backfilled_rows_survive_the_scope_predicate(legacy_db):
    """Rows must match ``warehouse_id IN (authorized)`` -- the deps.py filter.

    Reproduces the shape of ``build_authorized_scope_predicates``: a NULL
    column silently drops every row, so the count under the filter is the
    thing that actually proves the fix.
    """
    path, _ = legacy_db
    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")
    wh = _default_wh_id(path)

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for table in ("materials", "inventory_records"):
            (total,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            (scoped,) = conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE tenant_id = 1 AND warehouse_id IN (?)",
                (wh,),
            ).fetchone()
            assert total > 0, f"{table} fixture is empty, test proves nothing"
            assert scoped == total, (
                f"{table}: {total - scoped} of {total} rows are invisible "
                "under the warehouse scope filter"
            )
    finally:
        conn.close()


def test_warehouse_backfill_uses_existing_default_not_hardcoded_one(legacy_db):
    """A pre-existing default warehouse with id != 1 must win."""
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO warehouses (id, slug, name, is_default, tenant_id) "
            "VALUES (7, 'main', '主仓', 1, 1)"
        )
        conn.commit()
    finally:
        conn.close()

    import legacy_db_migration as mod

    mod.migrate(path, log=lambda _m: None)

    assert _distinct_warehouse_ids(path, "materials") == [7]
    assert _distinct_warehouse_ids(path, "inventory_records") == [7]


def test_warehouse_backfill_is_idempotent_and_keeps_explicit_values(legacy_db):
    """Only NULL rows are touched; a second run reports no change."""
    path, _ = legacy_db
    import legacy_db_migration as mod

    mod.migrate(path, log=lambda _m: None)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("UPDATE materials SET warehouse_id = 99 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    second = mod.migrate(path, log=lambda _m: None)

    assert [c for c in second.changes if "warehouse_id" in c] == []
    assert _distinct_warehouse_ids(path, "materials") == [1, 99]


def test_warehouse_backfill_counts_are_reported(legacy_db):
    path, _ = legacy_db
    import legacy_db_migration as mod

    result = mod.migrate(path, log=lambda _m: None)

    backfills = [c for c in result.changes if "backfilled warehouse_id" in c]
    assert any(c.startswith("materials:") for c in backfills), backfills
    assert any(c.startswith("inventory_records:") for c in backfills), backfills


# --------------------------------------------------------------------------
# 7. inventory_records.reason is preserved before c5d6e7f8a9b0 drops it
# --------------------------------------------------------------------------
def _set_reason(path: Path, rows: dict[int, tuple[str | None, str | None]]) -> None:
    """Write ``{record_id: (reason, reason_note)}`` into the legacy DB."""
    conn = sqlite3.connect(str(path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(inventory_records)")}
        if "reason" not in cols:
            conn.execute("ALTER TABLE inventory_records ADD COLUMN reason VARCHAR(255)")
        for rec_id, (reason, note) in rows.items():
            conn.execute(
                "UPDATE inventory_records SET reason = ? WHERE id = ?",
                (reason, rec_id),
            )
            if "reason_note" in cols:
                conn.execute(
                    "UPDATE inventory_records SET reason_note = ? WHERE id = ?",
                    (note, rec_id),
                )
        conn.commit()
    finally:
        conn.close()


def _reason_notes(path: Path) -> dict[int, str | None]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return dict(
            conn.execute("SELECT id, reason_note FROM inventory_records")
        )
    finally:
        conn.close()


def test_reason_text_survives_the_full_chain(legacy_db):
    """``reason`` text must be readable in ``reason_note`` after head."""
    path, _ = legacy_db
    _set_reason(path, {1: ("客户退货补入库", None), 2: ("生产领料", None)})

    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")

    assert "reason" not in _cols(path, "inventory_records"), (
        "c5d6e7f8a9b0 should still drop the column -- the migration is unchanged"
    )
    notes = _reason_notes(path)
    assert notes[1] == "客户退货补入库"
    assert notes[2] == "生产领料"


def test_reason_does_not_overwrite_existing_reason_note(legacy_db):
    path, _ = legacy_db
    _set_reason(
        path,
        {
            1: ("旧字段文本", "新字段已有内容"),
            2: ("旧字段文本", ""),
            3: (None, None),
        },
    )

    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")

    notes = _reason_notes(path)
    assert notes[1] == "新字段已有内容", "existing reason_note was clobbered"
    assert notes[2] == "旧字段文本", "empty reason_note should be filled"
    assert notes[3] is None


def test_reason_preserved_when_replacement_columns_are_missing(legacy_db):
    """An old bootstrap may lack reason_note / reason_category entirely."""
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        _rebuild_legacy_shape(
            conn, "inventory_records", ["reason_note", "reason_category"], None
        )
        conn.commit()
    finally:
        conn.close()
    assert "reason_note" not in _cols(path, "inventory_records")
    _set_reason(path, {1: ("补货", None)})

    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")

    assert _reason_notes(path)[1] == "补货"


def test_reason_preservation_is_reported_and_idempotent(legacy_db):
    path, _ = legacy_db
    _set_reason(path, {1: ("客户退货", None)})
    import legacy_db_migration as mod

    first = mod.migrate(path, log=lambda _m: None)
    assert any("preserved reason -> reason_note" in c for c in first.changes)

    second = mod.migrate(path, log=lambda _m: None)
    assert [c for c in second.changes if "reason" in c] == []


# --------------------------------------------------------------------------
# 8. ambiguous batch stock -> refuse to auto-migrate, write nothing
# --------------------------------------------------------------------------
def _add_batches(path: Path, rows: list[tuple[str, int, int]]) -> None:
    """Insert ``(batch_no, material_id, quantity)`` active batches."""
    conn = sqlite3.connect(str(path))
    try:
        for batch_no, material_id, qty in rows:
            conn.execute(
                "INSERT INTO batches "
                "(batch_no, material_id, quantity, initial_quantity, is_exhausted) "
                "VALUES (?, ?, ?, ?, 0)",
                (batch_no, material_id, qty, qty),
            )
        conn.commit()
    finally:
        conn.close()


def _digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_diverging_batches_refuse_auto_migration(legacy_db):
    path, _ = legacy_db
    # materials 1..3 have quantity 10/20/30; give material 1 a short batch.
    _add_batches(path, [("B-1", 1, 4)])
    before = _digest(path)

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacyMigrationAmbiguity) as exc:
        _recover(path)

    msg = str(exc.value)
    assert "materials.quantity=10" in msg and "active batch sum=4" in msg
    assert "-m backend.legacy_db_migration" in msg
    # Nothing written at all -- not even a backup.
    assert _backups(path) == []
    assert _digest(path) == before
    assert "tenant_id" not in _cols(path, "users")


def test_empty_batches_is_not_ambiguous(legacy_db):
    """No batch history -> d6e7f8a9b0c1's synthesis is the intended semantics."""
    path, before = legacy_db
    import legacy_db_migration as mod

    assert mod.find_batch_quantity_divergence(path) == []

    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        (mat_sum,) = conn.execute("SELECT SUM(quantity) FROM materials").fetchone()
        (batch_sum,) = conn.execute(
            "SELECT SUM(quantity) FROM batches WHERE is_exhausted = 0"
        ).fetchone()
        (synth,) = conn.execute(
            "SELECT COUNT(*) FROM batches WHERE batch_no LIKE 'LEGACY-MIG-d6e7-%'"
        ).fetchone()
    finally:
        conn.close()
    assert batch_sum == mat_sum
    assert synth > 0
    assert _counts(path) == before


def test_reconciled_batches_are_not_ambiguous(legacy_db):
    """Batch history that agrees with materials.quantity migrates normally."""
    path, _ = legacy_db
    _add_batches(path, [("B-1", 1, 10), ("B-2", 2, 20), ("B-3", 3, 30)])

    import legacy_db_migration as mod

    assert mod.find_batch_quantity_divergence(path) == []
    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")
    assert "tenant_id" in _cols(path, "users")


def test_divergence_report_lists_material_details(legacy_db):
    path, _ = legacy_db
    _add_batches(path, [("B-1", 1, 4), ("B-2", 2, 7)])
    import legacy_db_migration as mod

    diverged = mod.find_batch_quantity_divergence(path)

    assert [(m[0], m[2], m[3]) for m in diverged] == [
        (1, 10, 4),
        (2, 20, 7),
        (3, 30, 0),
    ]


# --------------------------------------------------------------------------
# 9. AUTO_MIGRATE_LEGACY_DB is parsed against a strict whitelist
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " enabled "])
def test_auto_migrate_flag_accepts_on_values(monkeypatch, value):
    import app as app_module

    monkeypatch.setenv("AUTO_MIGRATE_LEGACY_DB", value)
    assert app_module._auto_migrate_legacy_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "NO", "off", " disabled "])
def test_auto_migrate_flag_accepts_off_values(monkeypatch, value):
    import app as app_module

    monkeypatch.setenv("AUTO_MIGRATE_LEGACY_DB", value)
    assert app_module._auto_migrate_legacy_enabled() is False


@pytest.mark.parametrize("value", ["flase", "disabled!", "00", "maybe", "2", "-1"])
def test_auto_migrate_flag_rejects_unknown_values(monkeypatch, value):
    """Fail-closed: an unrecognised value must not silently mean 'on'."""
    import app as app_module

    monkeypatch.setenv("AUTO_MIGRATE_LEGACY_DB", value)
    with pytest.raises(RuntimeError) as exc:
        app_module._auto_migrate_legacy_enabled()

    msg = str(exc.value)
    assert repr(value) in msg
    assert "enable:" in msg and "disable:" in msg


def test_malformed_flag_refuses_startup_on_legacy_db(legacy_db, monkeypatch):
    path, _ = legacy_db
    monkeypatch.setenv("AUTO_MIGRATE_LEGACY_DB", "flase")

    with pytest.raises(RuntimeError) as exc:
        _recover(path)

    assert "unrecognised value" in str(exc.value)
    assert _backups(path) == []
    assert "tenant_id" not in _cols(path, "users")


def test_malformed_flag_refuses_startup_on_fresh_db(tmp_path, monkeypatch):
    """The value is validated on every boot, not only when a legacy DB shows up."""
    path = tmp_path / "fresh.db"
    sqlite3.connect(str(path)).close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DEPLOY_MODE", "single_tenant")
    monkeypatch.setenv("AUTO_MIGRATE_LEGACY_DB", "flase")
    import db as db_module

    db_module.reset_engine()
    try:
        with pytest.raises(RuntimeError) as exc:
            _recover(path)
        assert "unrecognised value" in str(exc.value)
    finally:
        db_module.reset_engine()


# --------------------------------------------------------------------------
# 10. table rebuild is self-checked
# --------------------------------------------------------------------------
def test_without_rowid_table_is_skipped_not_corrupted(legacy_db):
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE erp_providers")
        # Full 1826e23835b6 column set (plus a local extra) so the table stays
        # equivalent to the revision being stamped -- the point of this test is
        # the WITHOUT ROWID skip, not a schema mismatch.
        conn.execute(
            "CREATE TABLE erp_providers ("
            "id INTEGER NOT NULL PRIMARY KEY, name TEXT, "
            "provider_name TEXT UNIQUE NOT NULL, class_name TEXT, "
            "filename TEXT, config TEXT, test_results TEXT, "
            "test_passed_at TIMESTAMP, is_active INTEGER, "
            "created_at TIMESTAMP, updated_at TIMESTAMP, "
            "note TEXT DEFAULT 'must be UNIQUE') WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO erp_providers (id, provider_name) VALUES (1, 'sap')"
        )
        conn.commit()
    finally:
        conn.close()

    import legacy_db_migration as mod

    logs: list[str] = []
    result = mod.migrate(path, log=logs.append)

    assert any(
        "erp_providers" in m and "WITHOUT ROWID" in m
        for m in logs
        if m.startswith("[warn]")
    ), logs
    assert not any("uq_erp_providers_provider_name" in c for c in result.changes)
    # Table untouched apart from the tenant_id patch; row still there.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert conn.execute(
            "SELECT provider_name, note FROM erp_providers"
        ).fetchall() == [("sap", "must be UNIQUE")]
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'erp_providers'"
        ).fetchone()
    finally:
        conn.close()
    assert "WITHOUT ROWID" in sql
    # The other rebuilds still happened.
    assert any("uq_users_username" in c for c in result.changes)


def test_unrewritable_unique_clause_rolls_everything_back(legacy_db):
    """``UNIQUE ON CONFLICT ...`` must fail loudly, leaving the DB untouched."""
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE erp_providers")
        conn.execute(
            "CREATE TABLE erp_providers (id INTEGER PRIMARY KEY, "
            "provider_name TEXT UNIQUE ON CONFLICT IGNORE, note TEXT)"
        )
        conn.execute(
            "INSERT INTO erp_providers (id, provider_name, note) "
            "VALUES (1, 'sap', 'keep me')"
        )
        conn.commit()
    finally:
        conn.close()
    before = _digest(path)

    import legacy_db_migration as mod

    with pytest.raises(sqlite3.Error):
        mod.migrate(path, log=lambda _m: None)

    assert _digest(path) == before, "migration must roll back completely"
    assert "tenant_id" not in _cols(path, "users")


class _MangledConn:
    """sqlite3 connection proxy that rewrites SQL on the way through.

    ``sqlite3.Connection.execute`` is read-only, so simulating a mis-cut
    ``CREATE TABLE`` (the thing the post-checks exist to catch) has to happen
    one level up.
    """

    def __init__(self, conn, mangle):
        self._conn = conn
        self._mangle = mangle

    def execute(self, sql, *args):
        if isinstance(sql, str):
            sql = self._mangle(sql)
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _rebuild_erp(conn, mod):
    return mod._rebuild_with_named_unique(
        conn,
        "erp_providers",
        "provider_name",
        "uq_erp_providers_provider_name",
        log=lambda _m: None,
    )


def test_rebuild_rejects_a_changed_column_set(legacy_db):
    """A CREATE TABLE rewrite that grows/loses a column must raise."""
    path, _ = legacy_db
    import legacy_db_migration as mod

    def mangle(sql: str) -> str:
        if sql.startswith("CREATE TABLE") and "erp_providers__legacy_rebuild" in sql:
            return sql.replace("(", "(smuggled_col TEXT, ", 1)
        return sql

    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(RuntimeError, match="changed the column set"):
            _rebuild_erp(_MangledConn(conn, mangle), mod)
    finally:
        conn.close()


def test_rebuild_rejects_a_changed_row_count(legacy_db):
    """A rewrite that silently drops rows must raise.

    Uses ``materials`` because the fixture already seeds rows there -- a
    rebuild that loses them is exactly the failure the check exists for.
    """
    path, _ = legacy_db
    import legacy_db_migration as mod

    def mangle(sql: str) -> str:
        if sql.startswith("INSERT INTO") and "materials__legacy_rebuild" in sql:
            return sql + " WHERE 1 = 0"
        return sql

    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(RuntimeError, match="changed the row count"):
            mod._rebuild_with_named_unique(
                _MangledConn(conn, mangle),
                "materials",
                "sku",
                "uq_materials_sku",
                log=lambda _m: None,
            )
    finally:
        conn.close()


def test_rebuild_rejects_a_missing_constraint(legacy_db):
    """If the named CONSTRAINT did not land, the rebuild must not be reported."""
    path, _ = legacy_db
    import legacy_db_migration as mod

    def mangle(sql: str) -> str:
        if sql.startswith("CREATE TABLE") and "erp_providers__legacy_rebuild" in sql:
            return sql.replace("CONSTRAINT uq_erp_providers_provider_name ", "")
        return sql

    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(RuntimeError, match="absent from"):
            _rebuild_erp(_MangledConn(conn, mangle), mod)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 11. the batch-ambiguity gate covers every path into `alembic upgrade head`
# --------------------------------------------------------------------------
# The bridge branch is not the only way a DB reaches d6e7f8a9b0c1: one whose
# ``alembic_version`` merely stopped at an earlier revision needs no bridge at
# all and used to walk straight past the gate.
_PRE_D6E7_REVISION = "c5d6e7f8a9b0"
_BATCH_SYNTHESIS_REVISION = "d6e7f8a9b0c1"


def _upgrade_to(path: Path, revision: str) -> None:
    """Run ``alembic upgrade <revision>`` against ``path`` explicitly."""
    prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    try:
        alembic_command.upgrade(_alembic_cfg(), revision)
    finally:
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url


def _bridged_db_at(path: Path, revision: str) -> None:
    """Take the legacy fixture through the bridge and up to ``revision``.

    The result needs no legacy bridge (``users.tenant_id`` exists) but sits
    before ``d6e7f8a9b0c1`` — the path the ambiguity gate used to miss.
    """
    import legacy_db_migration as mod

    mod.migrate(path, log=lambda _m: None)
    _upgrade_to(path, revision)
    for stale in _backups(path):
        stale.unlink()


def _run_app_startup(path: Path, env: dict[str, str] | None = None):
    """Boot the real app against ``path`` out of process; return the result."""
    script = textwrap.dedent(
        f"""
        import os, sys
        os.environ["DATABASE_PATH"] = {str(path)!r}
        os.environ["DATABASE_URL"] = "sqlite:///" + {str(path)!r}
        os.environ["DEPLOY_MODE"] = "single_tenant"
        os.environ["INIT_MOCK_DATA"] = "false"
        sys.path.insert(0, {str(BACKEND_DIR)!r})
        os.chdir({str(BACKEND_DIR)!r})
        from fastapi.testclient import TestClient
        from app import app
        with TestClient(app) as c:
            r = c.get("/health")
            print("HEALTH", r.status_code, r.text)
        """
    )
    proc_env = dict(os.environ)
    proc_env.pop("ALLOW_AMBIGUOUS_BATCH_MIGRATION", None)
    proc_env.update(env or {})
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=300,
        env=proc_env,
    )


def _synthesised_batches(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT batch_no FROM batches WHERE batch_no LIKE 'LEGACY-MIG-d6e7-%'"
            )
        ]
    finally:
        conn.close()


def _stamped_revision(path: Path) -> str | None:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None  # table absent -> nothing was ever stamped
        return row[0] if row else None
    finally:
        conn.close()


def test_revision_precedes_uses_the_alembic_graph(legacy_db):
    """Ordering comes from alembic's revision map, not string comparison."""
    import app as app_module

    cfg = _alembic_cfg()
    assert app_module._revision_precedes(cfg, None, _BATCH_SYNTHESIS_REVISION)
    assert app_module._revision_precedes(
        cfg, INITIAL_SCHEMA_REVISION, _BATCH_SYNTHESIS_REVISION
    )
    assert app_module._revision_precedes(
        cfg, _PRE_D6E7_REVISION, _BATCH_SYNTHESIS_REVISION
    )
    # The target itself and its descendants are not "before" it.
    assert not app_module._revision_precedes(
        cfg, _BATCH_SYNTHESIS_REVISION, _BATCH_SYNTHESIS_REVISION
    )
    assert not app_module._revision_precedes(
        cfg, "e7f8a9b0c1d2", _BATCH_SYNTHESIS_REVISION
    )


def test_gate_blocks_non_bridge_path_stopped_before_d6e7(legacy_db):
    """No bridge needed, stopped before d6e7 -> still refused, no synthesis."""
    path, _ = legacy_db
    _bridged_db_at(path, _PRE_D6E7_REVISION)
    _add_batches(path, [("B-1", 1, 4)])  # material 1 has quantity 10
    assert _stamped_revision(path) == _PRE_D6E7_REVISION
    before = _digest(path)

    import app as app_module
    import legacy_db_migration as mod

    with pytest.raises(mod.LegacyMigrationAmbiguity) as exc:
        app_module._assert_batch_synthesis_unambiguous(_alembic_cfg())

    assert "materials.quantity=10" in str(exc.value)
    assert _synthesised_batches(path) == []
    assert _stamped_revision(path) == _PRE_D6E7_REVISION
    assert _digest(path) == before
    assert _backups(path) == []


def test_gate_blocks_non_bridge_path_via_full_startup(tmp_path):
    """End-to-end: the real startup hook refuses and synthesises nothing."""
    path = tmp_path / "warehouse.db"
    _make_legacy_db(path)
    _bridged_db_at(path, _PRE_D6E7_REVISION)
    _add_batches(path, [("B-1", 1, 4)])

    proc = _run_app_startup(path)

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "LegacyMigrationAmbiguity" in proc.stderr, proc.stdout + proc.stderr
    assert _synthesised_batches(path) == []
    assert _stamped_revision(path) == _PRE_D6E7_REVISION


def test_gate_waived_by_override_env(legacy_db, monkeypatch):
    """The explicit override lets a reviewed divergence through."""
    path, _ = legacy_db
    _bridged_db_at(path, _PRE_D6E7_REVISION)
    _add_batches(path, [("B-1", 1, 4)])
    monkeypatch.setenv("ALLOW_AMBIGUOUS_BATCH_MIGRATION", "1")

    import app as app_module

    app_module._assert_batch_synthesis_unambiguous(_alembic_cfg())
    _upgrade_to(path, "head")

    assert _synthesised_batches(path), "d6e7 should have run once waived"
    assert _stamped_revision(path) is not None


def test_gate_does_not_fire_after_d6e7(legacy_db):
    """A DB already past d6e7 is never re-gated (the synthesis is behind it)."""
    path, _ = legacy_db
    _bridged_db_at(path, "head")
    _add_batches(path, [("B-9", 1, 4)])

    import app as app_module

    app_module._assert_batch_synthesis_unambiguous(_alembic_cfg())


# ---- the manual CLI entry points are gated too ---------------------------
def test_cli_module_refuses_ambiguous_db(legacy_db):
    """``python -m backend.legacy_db_migration`` must not bypass the gate."""
    path, _ = legacy_db
    _add_batches(path, [("B-1", 1, 4)])
    before = _digest(path)

    proc = subprocess.run(
        [sys.executable, "-m", "backend.legacy_db_migration", str(path)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "REFUSED" in proc.stderr, proc.stdout + proc.stderr
    assert "materials.quantity=10" in proc.stderr
    assert _digest(path) == before
    assert _backups(path) == []


def test_cli_script_refuses_ambiguous_db(legacy_db):
    """``scripts/migrate_legacy_db.py`` shares the same gate."""
    path, _ = legacy_db
    _add_batches(path, [("B-1", 1, 4)])
    before = _digest(path)

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "migrate_legacy_db.py"), str(path)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "REFUSED" in proc.stderr, proc.stdout + proc.stderr
    assert _digest(path) == before
    assert _backups(path) == []


def test_cli_honours_the_override_env(legacy_db, monkeypatch):
    path, _ = legacy_db
    _add_batches(path, [("B-1", 1, 4)])
    monkeypatch.setenv("ALLOW_AMBIGUOUS_BATCH_MIGRATION", "1")
    import legacy_db_migration as mod

    result = mod.migrate(path, log=lambda _m: None)

    assert result.changed
    assert "tenant_id" in _cols(path, "users")


@pytest.mark.parametrize("value", ["flase", "maybe", "2"])
def test_override_env_rejects_unknown_values(monkeypatch, value):
    monkeypatch.setenv("ALLOW_AMBIGUOUS_BATCH_MIGRATION", value)
    import legacy_db_migration as mod

    with pytest.raises(RuntimeError, match="unrecognised value"):
        mod.allow_ambiguous_batch_migration()


def test_override_env_default_is_off(monkeypatch):
    import legacy_db_migration as mod

    monkeypatch.delenv("ALLOW_AMBIGUOUS_BATCH_MIGRATION", raising=False)
    assert mod.allow_ambiguous_batch_migration() is False
    monkeypatch.setenv("ALLOW_AMBIGUOUS_BATCH_MIGRATION", "")
    assert mod.allow_ambiguous_batch_migration() is False
    monkeypatch.setenv("ALLOW_AMBIGUOUS_BATCH_MIGRATION", "off")
    assert mod.allow_ambiguous_batch_migration() is False


# --------------------------------------------------------------------------
# 12. missing / ambiguous default warehouse -> refuse, never stamp NULLs
# --------------------------------------------------------------------------
def test_missing_warehouses_table_refuses_instead_of_leaving_nulls(legacy_db):
    """Dropping ``warehouses`` used to downgrade to a warning + NULL columns."""
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("DROP TABLE warehouses")
        conn.commit()
    finally:
        conn.close()

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacyMigrationAmbiguity) as exc:
        mod.migrate(path, log=lambda _m: None)

    msg = str(exc.value)
    assert "no default warehouse could be determined" in msg
    assert "materials" in msg
    assert "LEGACY_MIGRATE_WAREHOUSE_ID" in msg
    # Whole migration rolled back: the schema patch is not half-applied.
    assert "tenant_id" not in _cols(path, "users")


def test_missing_warehouses_table_refuses_at_startup(legacy_db):
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("DROP TABLE warehouses")
        conn.commit()
    finally:
        conn.close()

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacyMigrationAmbiguity):
        _recover(path)

    assert "tenant_id" not in _cols(path, "users")


def _insert_warehouse(path: Path, wid: int, slug: str, is_default: int) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO warehouses (id, slug, name, is_default, tenant_id) "
            "VALUES (?, ?, ?, ?, 1)",
            (wid, slug, slug, is_default),
        )
        conn.commit()
    finally:
        conn.close()


def test_two_default_warehouses_refuse_and_list_candidates(legacy_db):
    """``ORDER BY is_default DESC`` used to pick an arbitrary one of them."""
    path, _ = legacy_db
    _insert_warehouse(path, 3, "north", 1)
    _insert_warehouse(path, 4, "south", 1)

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacyMigrationAmbiguity) as exc:
        mod.migrate(path, log=lambda _m: None)

    msg = str(exc.value)
    assert "2 warehouses are flagged is_default=1" in msg
    assert "id=3" in msg and "'north'" in msg
    assert "id=4" in msg and "'south'" in msg
    assert "LEGACY_MIGRATE_WAREHOUSE_ID" in msg
    assert "tenant_id" not in _cols(path, "users")


def test_explicit_warehouse_id_resolves_the_ambiguity(legacy_db, monkeypatch):
    path, _ = legacy_db
    _insert_warehouse(path, 3, "north", 1)
    _insert_warehouse(path, 4, "south", 1)
    monkeypatch.setenv("LEGACY_MIGRATE_WAREHOUSE_ID", "4")

    import legacy_db_migration as mod

    result = mod.migrate(path, log=lambda _m: None)

    assert result.changed
    assert _null_warehouse_counts(path) == {t: 0 for t in _WAREHOUSE_SCOPED}
    assert _distinct_warehouse_ids(path, "materials") == [4]
    assert _distinct_warehouse_ids(path, "inventory_records") == [4]


def test_explicit_warehouse_id_must_exist(legacy_db, monkeypatch):
    path, _ = legacy_db
    monkeypatch.setenv("LEGACY_MIGRATE_WAREHOUSE_ID", "99")

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacyMigrationAmbiguity) as exc:
        mod.migrate(path, log=lambda _m: None)

    assert "does not exist in 'warehouses'" in str(exc.value)
    assert "tenant_id" not in _cols(path, "users")


def test_explicit_warehouse_id_must_be_an_integer(legacy_db, monkeypatch):
    path, _ = legacy_db
    monkeypatch.setenv("LEGACY_MIGRATE_WAREHOUSE_ID", "main")

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacyMigrationAmbiguity, match="not an integer"):
        mod.migrate(path, log=lambda _m: None)


def test_several_warehouses_without_a_default_are_ambiguous(legacy_db):
    path, _ = legacy_db
    _insert_warehouse(path, 3, "north", 0)
    _insert_warehouse(path, 4, "south", 0)

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacyMigrationAmbiguity) as exc:
        mod.migrate(path, log=lambda _m: None)

    assert "holds 2 rows and none is flagged is_default" in str(exc.value)


def test_single_warehouse_without_default_flag_is_used(legacy_db):
    path, _ = legacy_db
    _insert_warehouse(path, 5, "only", 0)

    import legacy_db_migration as mod

    mod.migrate(path, log=lambda _m: None)

    assert _distinct_warehouse_ids(path, "materials") == [5]


# --------------------------------------------------------------------------
# 13. the rebuild self-check compares full column definitions, not just names
# --------------------------------------------------------------------------
def _md5(path: Path) -> str:
    import hashlib

    return hashlib.md5(path.read_bytes()).hexdigest()


def _table_info(path: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [tuple(r[1:]) for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_default_literal_containing_unique_is_caught_by_the_self_check(legacy_db):
    """The reviewer's counterexample: name set + row count + constraint all pass.

    ``DEFAULT 'NOT UNIQUE YET'`` sits before the inline ``UNIQUE`` on the same
    column, so the (deliberately not SQL-aware) rewrite cuts inside the string
    literal and turns the default into ``'NOT YET'``. Column names, row count
    and the new CONSTRAINT name are all unaffected — only a full ``table_info``
    comparison notices.
    """
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE erp_providers")
        conn.execute(
            "CREATE TABLE erp_providers ("
            "id INTEGER PRIMARY KEY, "
            "provider_name TEXT DEFAULT 'NOT UNIQUE YET' UNIQUE, "
            "note TEXT)"
        )
        conn.execute(
            "INSERT INTO erp_providers (id, provider_name, note) "
            "VALUES (1, 'sap', 'keep me')"
        )
        conn.commit()
    finally:
        conn.close()
    before_md5 = _md5(path)
    before_info = _table_info(path, "erp_providers")

    import legacy_db_migration as mod

    with pytest.raises(RuntimeError, match="changed a column definition"):
        mod.migrate(path, log=lambda _m: None)

    assert _md5(path) == before_md5, "the whole migration must roll back"
    assert _table_info(path, "erp_providers") == before_info
    assert "tenant_id" not in _cols(path, "users")


def test_self_check_reports_the_mangled_default_value(legacy_db):
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE erp_providers")
        conn.execute(
            "CREATE TABLE erp_providers ("
            "id INTEGER PRIMARY KEY, "
            "provider_name TEXT DEFAULT 'NOT UNIQUE YET' UNIQUE)"
        )
        conn.commit()
    finally:
        conn.close()

    import legacy_db_migration as mod

    with pytest.raises(RuntimeError) as exc:
        mod.migrate(path, log=lambda _m: None)

    msg = str(exc.value)
    assert "provider_name:" in msg
    assert "'NOT UNIQUE YET'" in msg and "'NOT YET'" in msg


def test_rebuild_rejects_a_dropped_not_null(legacy_db):
    """A rewrite that loses NOT NULL must raise even though names match."""
    path, _ = legacy_db
    import legacy_db_migration as mod

    def mangle(sql: str) -> str:
        if sql.startswith("CREATE TABLE") and "erp_providers__legacy_rebuild" in sql:
            return sql.replace("NOT NULL", "")
        return sql

    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(RuntimeError, match="changed a column definition"):
            _rebuild_erp(_MangledConn(conn, mangle), mod)
    finally:
        conn.close()


def test_rebuild_rejects_a_dropped_foreign_key(legacy_db):
    """Losing an FK during the rebuild must abort the migration."""
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE erp_providers")
        conn.execute(
            "CREATE TABLE erp_providers ("
            "id INTEGER PRIMARY KEY, provider_name TEXT UNIQUE, "
            "tenant_id INTEGER, "
            "CONSTRAINT fk_erp_tenant FOREIGN KEY(tenant_id) "
            "REFERENCES tenants (id))"
        )
        conn.commit()
    finally:
        conn.close()

    import legacy_db_migration as mod

    def mangle(sql: str) -> str:
        if sql.startswith("CREATE TABLE") and "erp_providers__legacy_rebuild" in sql:
            return re.sub(
                r",\s*CONSTRAINT fk_erp_tenant FOREIGN KEY\(tenant_id\)\s*"
                r"REFERENCES tenants \(id\)",
                "",
                sql,
            )
        return sql

    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(RuntimeError, match="changed the foreign keys"):
            _rebuild_erp(_MangledConn(conn, mangle), mod)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 14. stamping asserts equivalence with the target revision
# --------------------------------------------------------------------------
def _retype_column(path: Path, table: str, column: str, new_type: str) -> None:
    """Rebuild ``table`` with ``column`` re-declared as ``new_type``."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        info = list(conn.execute(f"PRAGMA table_info({table})"))
        defs = []
        for _cid, name, ctype, notnull, dflt, pk in info:
            piece = f'"{name}" {new_type if name == column else (ctype or "TEXT")}'
            if pk:
                piece += " PRIMARY KEY"
            elif notnull:
                piece += " NOT NULL"
            if dflt is not None:
                piece += f" DEFAULT {dflt}"
            defs.append(piece)
        cols = ", ".join(f'"{r[1]}"' for r in info)
        conn.execute(f'CREATE TABLE "{table}__t" ({", ".join(defs)})')
        conn.execute(f'INSERT INTO "{table}__t" ({cols}) SELECT {cols} FROM "{table}"')
        conn.execute(f'DROP TABLE "{table}"')
        conn.execute(f'ALTER TABLE "{table}__t" RENAME TO "{table}"')
        conn.commit()
    finally:
        conn.close()


def test_reference_schema_is_built_from_the_real_migrations(legacy_db):
    import legacy_db_migration as mod

    ref = mod.reference_schema(INITIAL_SCHEMA_REVISION)

    assert ref["users"]["tenant_id"].upper().startswith("INTEGER")
    assert "warehouse_id" in ref["materials"]
    assert "reason" in ref["inventory_records"]
    # Cached: a second call must not rebuild anything.
    assert mod.reference_schema(INITIAL_SCHEMA_REVISION) is ref


def test_declared_type_families_tolerate_legacy_affinities():
    """Old bootstraps wrote bare affinities; those are not a mismatch."""
    import legacy_db_migration as mod

    same = [
        ("VARCHAR(64)", "TEXT"),
        ("JSON", "TEXT"),
        ("BOOLEAN", "INTEGER"),
        ("DATETIME", "TIMESTAMP"),
    ]
    for a, b in same:
        assert mod._type_family(a) == mod._type_family(b), (a, b)
    assert mod._type_family("INTEGER") != mod._type_family("TEXT")


def test_incompatible_column_type_refuses_the_stamp(legacy_db):
    """Same column name, wrong type -> the stamp would be a lie."""
    path, _ = legacy_db
    _retype_column(path, "materials", "quantity", "TEXT")
    before = _md5(path)

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacySchemaMismatch) as exc:
        mod.migrate(path, log=lambda _m: None)

    msg = str(exc.value)
    assert "incompatible column types" in msg
    assert "materials.quantity" in msg
    assert "expected INTEGER" in msg and "actual TEXT" in msg
    assert _md5(path) == before, "the migration must roll back entirely"
    assert "tenant_id" not in _cols(path, "users")
    assert _stamped_revision(path) is None


def test_missing_column_refuses_the_stamp(legacy_db):
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        _rebuild_legacy_shape(conn, "materials", ["location"], "sku")
        conn.commit()
    finally:
        conn.close()

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacySchemaMismatch) as exc:
        mod.migrate(path, log=lambda _m: None)

    assert "columns missing locally" in str(exc.value)
    assert "materials.location" in str(exc.value)


def test_orphan_foreign_key_rows_refuse_the_stamp(legacy_db):
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute(
            "INSERT INTO batch_consumptions (id, record_id, batch_id, quantity) "
            "VALUES (1, 4242, 4243, 1)"
        )
        conn.commit()
    finally:
        conn.close()
    before = _md5(path)

    import legacy_db_migration as mod

    with pytest.raises(mod.LegacySchemaMismatch) as exc:
        mod.migrate(path, log=lambda _m: None)

    msg = str(exc.value)
    assert "orphan foreign-key rows" in msg
    assert "batch_consumptions" in msg
    assert _md5(path) == before
    assert "tenant_id" not in _cols(path, "users")


def test_extra_local_columns_only_warn(legacy_db):
    """A legacy deployment's own extra column must not block the migration."""
    path, _ = legacy_db
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("ALTER TABLE materials ADD COLUMN local_note TEXT")
        conn.commit()
    finally:
        conn.close()

    import legacy_db_migration as mod

    logs: list[str] = []
    result = mod.migrate(path, log=logs.append)

    assert result.changed
    assert any("materials.local_note" in m for m in logs if m.startswith("[warn]")), logs
    assert "tenant_id" in _cols(path, "users")


def test_equivalence_check_passes_on_the_plain_fixture(legacy_db):
    """The happy path must not trip the new gate."""
    path, before = legacy_db

    _recover(path)
    alembic_command.upgrade(_alembic_cfg(), "head")

    assert _counts(path) == before
    assert _null_warehouse_counts(path) == {t: 0 for t in _WAREHOUSE_SCOPED}
