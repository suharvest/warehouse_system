#!/usr/bin/env python3
"""Migrate a pre-multi-tenant warehouse SQLite DB to the initial-schema head.

Use case
========
Old deployments shipped a schema where ``users``, ``materials``, ``batches``,
``inventory_records``, ``contacts`` etc. had no ``tenant_id`` / ``warehouse_id``
columns. The consolidated ``1826e23835b6_initial_schema`` migration assumes
either a fresh DB or one that already matches it — it cannot ALTER legacy
tables.

When such a DB is mounted into a current container, startup fails with either:

  * ``sqlite3.OperationalError: table face_auth_logs already exists`` — because
    ``alembic_version`` was wiped and alembic tries to re-run initial schema.
  * ``sqlite3.OperationalError: no such column: tenant_id`` — because the app
    validates ``users.tenant_id`` which the legacy schema never had.

This module bridges that gap. It is idempotent and safe to re-run.

What it does
============
1. Backs up the DB file next to itself.
2. Creates a default tenant row (id=1) if ``tenants`` is empty.
3. Creates a default warehouse row (id=1) if ``warehouses`` is empty or missing.
4. For each legacy business table, ALTERs in any missing ``tenant_id`` /
   ``warehouse_id`` columns, backfills ``tenant_id`` to 1 and ``warehouse_id``
   to the default warehouse's actual id (looked up, not hardcoded). Legacy
   rows predate the warehouse split, so they all belong to that one
   warehouse; leaving them NULL would hide them from every warehouse-scoped
   query in ``backend/deps.py``.
5. Stamps ``alembic_version`` to ``1826e23835b6`` (initial schema) so the
   normal alembic chain can apply the incremental migrations that follow.

After running this, start the container normally — the startup hook will run
``alembic upgrade head`` and the app will validate successfully.

This module is the single implementation. Two entry points wrap it:

* ``backend.app._recover_legacy_alembic_state`` calls :func:`migrate`
  automatically at startup in single-tenant SQLite deployments.
* CLI, from the repo root on a host::

      uv run python scripts/migrate_legacy_db.py /path/to/warehouse.db

  or from inside the container (``scripts/`` is not shipped in the image)::

      /app/.venv/bin/python -m backend.legacy_db_migration /data/warehouse.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

INITIAL_SCHEMA_REVISION = "1826e23835b6"

# Tables whose UNIQUE constraints later migrations need to drop *by name*.
# Old DBs created the column inline (`username TEXT UNIQUE`), which produces
# an anonymous ``sqlite_autoindex_<table>_<n>`` that alembic's
# ``drop_constraint('uq_…')`` cannot find — chain fails. We rebuild these
# tables so the UNIQUE has the expected name.
NAMED_UNIQUE_TARGETS: list[tuple[str, str, str]] = [
    ("users", "username", "uq_users_username"),
    ("materials", "sku", "uq_materials_sku"),
    ("batches", "batch_no", "uq_batches_batch_no"),
    ("warehouses", "slug", "uq_warehouses_slug"),
    ("erp_providers", "provider_name", "uq_erp_providers_provider_name"),
]

LEGACY_TABLE_PATCHES: dict[str, list[tuple[str, str]]] = {
    "users": [("tenant_id", "INTEGER DEFAULT 1")],
    "materials": [
        ("tenant_id", "INTEGER DEFAULT 1"),
        ("warehouse_id", "INTEGER"),
    ],
    "batches": [
        ("tenant_id", "INTEGER DEFAULT 1"),
        ("warehouse_id", "INTEGER"),
    ],
    "inventory_records": [
        ("tenant_id", "INTEGER DEFAULT 1"),
        ("warehouse_id", "INTEGER"),
        # ``reason`` is present in 1826e23835b6 and dropped by c5d6e7f8a9b0.
        # The legacy bootstrap never created it, so alembic's
        # ``batch_op.drop_column('reason')`` blows up with KeyError while
        # replaying the chain. Re-add it so the DB really matches the
        # revision we are about to stamp.
        ("reason", "VARCHAR(255)"),
    ],
    "contacts": [
        ("tenant_id", "INTEGER DEFAULT 1"),
        ("warehouse_id", "INTEGER"),
    ],
    "mcp_connections": [
        ("tenant_id", "INTEGER DEFAULT 1"),
        ("warehouse_id", "INTEGER"),
    ],
    "api_keys": [
        ("tenant_id", "INTEGER DEFAULT 1"),
        ("warehouse_id", "INTEGER"),
    ],
    "erp_providers": [("tenant_id", "INTEGER DEFAULT 1")],
    # NOTE: ``batch_consumptions`` is deliberately absent. At revision
    # 1826e23835b6 it has no tenant_id/warehouse_id — those are added later by
    # b2c3d4e5f6a7. Pre-adding them here makes that migration fail with
    # "duplicate column name: tenant_id". This table must be left alone.
}


@dataclass
class MigrationResult:
    """Outcome of one :func:`migrate` run."""

    db_path: Path
    backup_path: Path
    revision: str
    changes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True when the run actually altered the schema/data (not a no-op)."""
        return bool(self.changes)


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _default_warehouse_id(conn: sqlite3.Connection) -> int | None:
    """Id of the warehouse legacy rows semantically belong to.

    The legacy schema has no warehouse concept at all: every row predates the
    split, so it belongs to the single warehouse the deployment actually has.
    Prefer the one flagged ``is_default``, else the lowest id. Returns None
    when there is no ``warehouses`` table or it is empty (nothing to point
    at — the caller then skips the backfill rather than inventing an id).

    Deliberately queried instead of hardcoded to 1: the bridge only seeds
    id=1 when the table is empty, and a legacy DB may already carry a default
    warehouse row with a different id.
    """
    if not _table_exists(conn, "warehouses"):
        return None
    cols = _table_cols(conn, "warehouses")
    if "is_default" in cols:
        row = conn.execute(
            "SELECT id FROM warehouses ORDER BY is_default DESC, id ASC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute("SELECT id FROM warehouses ORDER BY id ASC LIMIT 1").fetchone()
    return int(row[0]) if row else None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _anonymous_unique_on(conn: sqlite3.Connection, table: str, col: str) -> bool:
    """True iff ``table`` declares UNIQUE on ``col`` without a CONSTRAINT name.

    SQLite always creates an ``sqlite_autoindex_…`` for any UNIQUE (named or
    not), so we can't tell them apart by index name. Instead, parse the
    original ``CREATE TABLE`` SQL: if there is no ``CONSTRAINT <name> UNIQUE``
    clause covering this column, the UNIQUE is anonymous and alembic's
    ``drop_constraint(name, …)`` will fail."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        return False
    sql = row[0]

    # Quick existence check via index_list — if there's no UNIQUE at all on this
    # column we have nothing to do regardless of naming.
    has_unique = False
    for idx_row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        _, name, unique, *_ = idx_row
        if not unique:
            continue
        idx_cols = [r[2] for r in conn.execute(f"PRAGMA index_info({name})")]
        if idx_cols == [col]:
            has_unique = True
            break
    if not has_unique:
        return False

    # Is there a named CONSTRAINT clause covering this column?
    named_pattern = re.compile(
        rf"CONSTRAINT\s+\w+\s+UNIQUE\s*\(\s*{re.escape(col)}\s*\)",
        flags=re.IGNORECASE,
    )
    return named_pattern.search(sql) is None


def _rebuild_with_named_unique(
    conn: sqlite3.Connection, table: str, col: str, constraint: str
) -> bool:
    """Rebuild ``table`` so its UNIQUE on ``col`` is named ``constraint``.

    Reads the original ``CREATE TABLE`` SQL from ``sqlite_master``, strips the
    inline ``UNIQUE`` from ``col``'s column definition, and appends a named
    constraint. Data preserved via ``INSERT … SELECT *``.

    Returns True when the table was actually rebuilt."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row:
        return False
    original_sql = row[0]

    # Strip the trailing UNIQUE keyword from `<col> ... UNIQUE` column def.
    # The optional quote char covers quoted identifiers (``"sku" VARCHAR(64)``,
    # as emitted by alembic/SQLAlchemy); the optional ``\(...\)`` covers sized
    # types (``VARCHAR(64) UNIQUE``); after that we stop at the next comma or
    # closing paren so we never consume a neighbouring column definition.
    col_pattern = re.compile(
        rf"(\b{re.escape(col)}\b[\"'`\]]?\s+\w+(?:\s*\([^)]*\))?[^,)]*?)"
        r"\s+UNIQUE\b",
        flags=re.IGNORECASE,
    )
    new_sql, n = col_pattern.subn(r"\1", original_sql, count=1)
    if n == 0:
        # UNIQUE wasn't inline — maybe declared via separate CREATE UNIQUE INDEX
        # or already named. Nothing to do.
        return False

    # Append the named constraint just before the closing `)`.
    new_sql = re.sub(
        r"\)\s*$",
        f", CONSTRAINT {constraint} UNIQUE ({col}))",
        new_sql.strip(),
        count=1,
    )

    tmp_table = f"{table}__legacy_rebuild"
    new_sql = new_sql.replace(
        f"CREATE TABLE {table}", f"CREATE TABLE {tmp_table}", 1
    )
    # Handle quoted variants too: `CREATE TABLE "users"` etc.
    new_sql = new_sql.replace(
        f'CREATE TABLE "{table}"', f'CREATE TABLE "{tmp_table}"', 1
    )

    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    col_list = ", ".join(f'"{c}"' for c in cols)

    # ``DROP TABLE`` takes the table's indexes and triggers with it, and the
    # rebuilt table only carries what the CREATE statement declares. Capture
    # everything sqlite_master holds for this table so we can put it back.
    # ``sql IS NOT NULL`` filters out the implicit ``sqlite_autoindex_*``
    # entries, which cannot (and must not) be re-issued by hand.
    attached = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
            (table,),
        )
    ]

    # A previous run may have died between CREATE and RENAME, leaving the
    # scratch table behind; without this the retry fails on "already exists".
    conn.execute(f'DROP TABLE IF EXISTS "{tmp_table}"')
    conn.execute(new_sql)
    conn.execute(
        f'INSERT INTO "{tmp_table}" ({col_list}) SELECT {col_list} FROM "{table}"'
    )
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{tmp_table}" RENAME TO "{table}"')
    for stmt in attached:
        conn.execute(stmt)
    return True


def needs_legacy_migration(db_path: Path) -> bool:
    """True when ``users`` exists but has no ``tenant_id`` column.

    Cheap pre-check so callers can avoid taking a backup of a DB that is
    already migrated.
    """
    if not Path(db_path).exists():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        if not _table_exists(conn, "users"):
            return False
        return "tenant_id" not in _table_cols(conn, "users")
    finally:
        conn.close()


def migrate(
    db_path: Path | str,
    *,
    log: Callable[[str], None] = print,
) -> MigrationResult:
    """Bring a legacy SQLite DB up to ``INITIAL_SCHEMA_REVISION``.

    Always takes a backup next to ``db_path`` before touching anything, then
    patches columns / rebuilds anonymous UNIQUEs / stamps ``alembic_version``.
    Idempotent: re-running on an already-migrated DB performs no schema change
    (``MigrationResult.changed`` is False), though a fresh backup is still
    written.

    Raises on any failure — callers must not swallow it, a half-migrated DB is
    worse than a refusal to start.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_suffix(db_path.suffix + f".bak.legacy_migrate_{ts}")
    # The app opens SQLite in WAL mode (backend/database.py), so committed
    # transactions can still live in ``<db>-wal`` instead of the .db file --
    # a container that died before a checkpoint leaves them there. Copying
    # only the .db would then produce a backup that silently lacks recent
    # data, and on a young WAL it can lack whole tables. The sqlite3 backup
    # API reads through the WAL and writes one consistent snapshot.
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(backup))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    log(f"[ok] backup -> {backup}")

    changes: list[str] = []
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")
    # One explicit transaction around the whole migration. Python's sqlite3
    # only opens an implicit transaction for DML, so bare DDL (CREATE/DROP/
    # ALTER during a table rebuild) would otherwise autocommit one statement at
    # a time and a crash mid-run could leave the DB in a shape no later run
    # knows how to finish. ``PRAGMA foreign_keys`` is deliberately set before
    # BEGIN -- it is a no-op inside a transaction.
    conn.execute("BEGIN")
    try:
        # Seed defaults column-by-column: ``tenants``/``warehouses`` declare
        # ``slug NOT NULL``, so a fixed 3-column INSERT blows up on a legacy DB
        # whose tenants table is empty.
        for table, values in (
            ("tenants", {"id": 1, "slug": "default", "name": "default"}),
            (
                "warehouses",
                {
                    "id": 1,
                    "slug": "default",
                    "name": "default",
                    "tenant_id": 1,
                    "is_default": 1,
                },
            ),
        ):
            if not _table_exists(conn, table):
                continue
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            if n:
                continue
            cols = _table_cols(conn, table)
            usable = {k: v for k, v in values.items() if k in cols}
            if "name" not in usable:
                continue
            placeholders = ", ".join("?" for _ in usable)
            conn.execute(
                f"INSERT INTO {table} ({', '.join(usable)}) "
                f"VALUES ({placeholders})",
                tuple(usable.values()),
            )
            changes.append(f"seeded default {table[:-1]} id=1")
            log(f"[ok] seeded default {table[:-1]} id=1")

        for table, patches in LEGACY_TABLE_PATCHES.items():
            if not _table_exists(conn, table):
                continue
            existing = _table_cols(conn, table)
            for col, decl in patches:
                if col in existing:
                    continue
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                changes.append(f"{table}: add column {col} {decl}")
                log(f"[ok] {table}: add column {col} {decl}")
                fill = 1 if col == "tenant_id" else None
                if fill is not None:
                    conn.execute(
                        f"UPDATE {table} SET {col} = ? WHERE {col} IS NULL",
                        (fill,),
                    )

        # Backfill ``warehouse_id``. Adding the column alone leaves every
        # legacy row NULL, and NULL matches neither ``warehouse_id = <id>``
        # nor ``warehouse_id IN (...)`` -- the scope predicates in
        # ``backend/deps.py`` that every list endpoint applies. The data would
        # still be in the DB but invisible in the UI. Legacy rows predate the
        # warehouse split, so they belong to the single default warehouse.
        # Only NULL rows are touched, so re-runs are no-ops.
        default_wh = _default_warehouse_id(conn)
        wh_tables = [
            t for t, patches in LEGACY_TABLE_PATCHES.items()
            if any(c == "warehouse_id" for c, _ in patches)
        ]
        if default_wh is None:
            log(
                "[warn] no warehouses row found -- skipping warehouse_id "
                "backfill; rows may be invisible to warehouse-scoped queries"
            )
        else:
            for table in wh_tables:
                if not _table_exists(conn, table):
                    continue
                if "warehouse_id" not in _table_cols(conn, table):
                    continue
                cur = conn.execute(
                    f"UPDATE {table} SET warehouse_id = ? "
                    f"WHERE warehouse_id IS NULL",
                    (default_wh,),
                )
                if cur.rowcount > 0:
                    changes.append(
                        f"{table}: backfilled warehouse_id={default_wh} "
                        f"for {cur.rowcount} row(s)"
                    )
                    log(
                        f"[ok] {table}: backfilled warehouse_id={default_wh} "
                        f"for {cur.rowcount} row(s)"
                    )

        for table, col, constraint in NAMED_UNIQUE_TARGETS:
            if not _table_exists(conn, table):
                continue
            if not _anonymous_unique_on(conn, table, col):
                continue
            if not _rebuild_with_named_unique(conn, table, col, constraint):
                continue
            changes.append(
                f"{table}: anonymous UNIQUE({col}) -> CONSTRAINT {constraint}"
            )
            log(
                f"[ok] {table}: anonymous UNIQUE({col}) -> "
                f"CONSTRAINT {constraint}"
            )

        current_rev = None
        if _table_exists(conn, "alembic_version"):
            row = conn.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            current_rev = row[0] if row else None
        else:
            conn.execute(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        if current_rev != INITIAL_SCHEMA_REVISION:
            conn.execute("DELETE FROM alembic_version")
            conn.execute(
                "INSERT INTO alembic_version (version_num) VALUES (?)",
                (INITIAL_SCHEMA_REVISION,),
            )
            changes.append(f"stamped alembic_version -> {INITIAL_SCHEMA_REVISION}")
            log(f"[ok] stamped alembic_version -> {INITIAL_SCHEMA_REVISION}")
        conn.commit()
    except BaseException:
        # Roll the whole thing back rather than leaving a half-migrated schema
        # behind. The caller turns this into a refusal to start.
        conn.rollback()
        raise
    finally:
        conn.close()

    log(
        "[done] run `alembic upgrade head` (or restart the container) to "
        "apply incremental migrations."
    )
    return MigrationResult(
        db_path=db_path,
        backup_path=backup,
        revision=INITIAL_SCHEMA_REVISION,
        changes=changes,
    )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Migrate a pre-multi-tenant warehouse SQLite DB."
    )
    p.add_argument("db_path", type=Path, help="Path to warehouse.db")
    args = p.parse_args(argv)
    try:
        migrate(args.db_path)
    except FileNotFoundError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
