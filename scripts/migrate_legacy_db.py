#!/usr/bin/env python3
"""Migrate a pre-multi-tenant warehouse SQLite DB to current head.

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

This script bridges that gap. It is idempotent and safe to re-run.

What it does
============
1. Backs up the DB file next to itself.
2. Creates a default tenant row (id=1) if ``tenants`` is empty.
3. Creates a default warehouse row (id=1) if ``warehouses`` is empty or missing.
4. For each legacy business table, ALTERs in any missing ``tenant_id`` /
   ``warehouse_id`` columns and backfills them to 1.
5. Stamps ``alembic_version`` to ``1826e23835b6`` (initial schema) so the
   normal alembic chain can apply the incremental migrations that follow.

After running this script, start the container normally — the startup hook
will run ``alembic upgrade head`` and the app will validate successfully.

Usage
=====
    uv run python scripts/migrate_legacy_db.py /path/to/warehouse.db

Or inside the container:
    .venv/bin/python /app/scripts/migrate_legacy_db.py /data/warehouse.db
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

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
    "batch_consumptions": [
        ("tenant_id", "INTEGER DEFAULT 1"),
        ("warehouse_id", "INTEGER"),
    ],
}


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


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
) -> None:
    """Rebuild ``table`` so its UNIQUE on ``col`` is named ``constraint``.

    Reads the original ``CREATE TABLE`` SQL from ``sqlite_master``, strips the
    inline ``UNIQUE`` from ``col``'s column definition, and appends a named
    constraint. Data preserved via ``INSERT … SELECT *``."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row:
        return
    original_sql = row[0]

    # Strip the trailing UNIQUE keyword from `<col> ... UNIQUE` column def.
    # We match up to the next comma or the closing paren so we don't accidentally
    # consume other column definitions.
    col_pattern = re.compile(
        rf"(\b{re.escape(col)}\b[^,)]*?)\s+UNIQUE\b",
        flags=re.IGNORECASE,
    )
    new_sql, n = col_pattern.subn(r"\1", original_sql, count=1)
    if n == 0:
        # UNIQUE wasn't inline — maybe declared via separate CREATE UNIQUE INDEX
        # or already named. Nothing to do.
        return

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

    conn.execute(new_sql)
    conn.execute(
        f'INSERT INTO "{tmp_table}" ({col_list}) SELECT {col_list} FROM "{table}"'
    )
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{tmp_table}" RENAME TO "{table}"')
    print(f"[ok] {table}: anonymous UNIQUE({col}) -> CONSTRAINT {constraint}")


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        sys.exit(f"DB not found: {db_path}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_suffix(db_path.suffix + f".bak.legacy_migrate_{ts}")
    shutil.copy2(db_path, backup)
    print(f"[ok] backup -> {backup}")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        if _table_exists(conn, "tenants"):
            (n,) = conn.execute("SELECT COUNT(*) FROM tenants").fetchone()
            if n == 0:
                conn.execute(
                    "INSERT INTO tenants (id, name) VALUES (1, 'default')"
                )
                print("[ok] seeded default tenant id=1")

        if _table_exists(conn, "warehouses"):
            (n,) = conn.execute("SELECT COUNT(*) FROM warehouses").fetchone()
            if n == 0:
                cols = _table_cols(conn, "warehouses")
                if {"id", "name", "tenant_id"}.issubset(cols):
                    conn.execute(
                        "INSERT INTO warehouses (id, name, tenant_id) "
                        "VALUES (1, 'default', 1)"
                    )
                    print("[ok] seeded default warehouse id=1")

        for table, patches in LEGACY_TABLE_PATCHES.items():
            if not _table_exists(conn, table):
                continue
            existing = _table_cols(conn, table)
            for col, decl in patches:
                if col in existing:
                    continue
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                print(f"[ok] {table}: add column {col} {decl}")
                fill = 1 if col == "tenant_id" else None
                if fill is not None:
                    conn.execute(
                        f"UPDATE {table} SET {col} = ? WHERE {col} IS NULL",
                        (fill,),
                    )

        for table, col, constraint in NAMED_UNIQUE_TARGETS:
            if not _table_exists(conn, table):
                continue
            if not _anonymous_unique_on(conn, table, col):
                continue
            _rebuild_with_named_unique(conn, table, col, constraint)

        if not _table_exists(conn, "alembic_version"):
            conn.execute(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        conn.execute("DELETE FROM alembic_version")
        conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            (INITIAL_SCHEMA_REVISION,),
        )
        print(f"[ok] stamped alembic_version -> {INITIAL_SCHEMA_REVISION}")
        conn.commit()
    finally:
        conn.close()

    print("[done] run `alembic upgrade head` (or restart the container) to "
          "apply incremental migrations.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("db_path", type=Path, help="Path to warehouse.db")
    args = p.parse_args()
    migrate(args.db_path)


if __name__ == "__main__":
    main()
