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
