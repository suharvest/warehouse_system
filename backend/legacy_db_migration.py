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
5. Copies any ``inventory_records.reason`` text into ``reason_note`` before
   revision ``c5d6e7f8a9b0`` drops the column without a data migration.
6. Stamps ``alembic_version`` to ``1826e23835b6`` (initial schema) so the
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
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

INITIAL_SCHEMA_REVISION = "1826e23835b6"

# The revision that synthesises ``LEGACY-MIG-d6e7-*`` batches from
# ``materials.quantity``. Any DB sitting *before* it must pass the ambiguity
# gate, otherwise that migration invents stock. Named here so both entry points
# (this module and ``backend/app.py``) refer to the same thing.
BATCH_SYNTHESIS_REVISION = "d6e7f8a9b0c1"

# Operator override for the ambiguity gate: set once the books have been
# reconciled by hand and the divergence is known to be acceptable.
ALLOW_AMBIGUOUS_ENV = "ALLOW_AMBIGUOUS_BATCH_MIGRATION"

# Operator override for the warehouse the legacy rows get backfilled into,
# for DBs where the bridge cannot decide on its own.
TARGET_WAREHOUSE_ENV = "LEGACY_MIGRATE_WAREHOUSE_ID"

# Same strict-whitelist parsing as ``AUTO_MIGRATE_LEGACY_DB`` in app.py: an
# unrecognised value is a hard error, never a silent "off" (or "on").
_ENV_ON_VALUES = ("1", "true", "yes", "on", "enable", "enabled")
_ENV_OFF_VALUES = ("0", "false", "no", "off", "disable", "disabled")

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
        # Present at 1826e23835b6, but a sufficiently old bootstrap may not
        # have them. They are also the destination of the ``reason``
        # preservation step below, which must not be skipped just because the
        # legacy DB never grew the replacement columns.
        ("reason_category", "VARCHAR(32)"),
        ("reason_note", "TEXT"),
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


class LegacyMigrationAmbiguity(RuntimeError):
    """The legacy DB's stock data is ambiguous — a human must decide.

    Raised *before* anything is written. Callers should surface the message and
    refuse to start rather than guessing.
    """


def _env_flag(name: str, *, default: bool) -> bool:
    """Parse ``name`` as a boolean against a strict whitelist.

    Unset / empty -> ``default``. Anything outside the whitelist raises: the
    operator's intent is unknown, and guessing it in the direction that
    rewrites their database is the wrong default.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _ENV_ON_VALUES:
        return True
    if value in _ENV_OFF_VALUES:
        return False
    raise RuntimeError(
        f"{name} has an unrecognised value {raw!r}. Refusing to proceed "
        "rather than guess what was meant.\n"
        f"  enable:  {', '.join(_ENV_ON_VALUES)}\n"
        f"  disable: {', '.join(_ENV_OFF_VALUES)}\n"
        f"  unset or empty: {'enabled' if default else 'disabled'} (default)\n"
        "Values are case-insensitive and surrounding whitespace is ignored."
    )


def allow_ambiguous_batch_migration() -> bool:
    """True when the operator explicitly waived the batch-ambiguity gate.

    Defaults to False. Ops set ``ALLOW_AMBIGUOUS_BATCH_MIGRATION=1`` after they
    have reconciled ``materials.quantity`` against the batch ledger by hand and
    accept that ``d6e7f8a9b0c1`` will synthesise batches for the remaining
    positive differences.
    """
    return _env_flag(ALLOW_AMBIGUOUS_ENV, default=False)


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


def _warehouse_rows(conn: sqlite3.Connection) -> list[tuple[int, str, int]]:
    """``(id, name, is_default)`` for every warehouse, lowest id first."""
    cols = _table_cols(conn, "warehouses")
    name_col = "name" if "name" in cols else "id"
    if "is_default" in cols:
        sql = (
            f"SELECT id, {name_col}, COALESCE(is_default, 0) FROM warehouses "
            "ORDER BY id ASC"
        )
    else:
        sql = f"SELECT id, {name_col}, 0 FROM warehouses ORDER BY id ASC"
    return [(int(r[0]), str(r[1]), int(r[2])) for r in conn.execute(sql)]


def _format_warehouse_candidates(rows: list[tuple[int, str, int]]) -> str:
    return "".join(
        f"    - id={wid} name={name!r}"
        + (" (is_default=1)" if is_default else "")
        + "\n"
        for wid, name, is_default in rows
    )


def _explicit_target_warehouse_id(conn: sqlite3.Connection) -> int | None:
    """``LEGACY_MIGRATE_WAREHOUSE_ID`` if set — validated against the DB."""
    raw = os.environ.get(TARGET_WAREHOUSE_ENV)
    if raw is None or raw.strip() == "":
        return None
    try:
        wanted = int(raw.strip())
    except ValueError:
        raise LegacyMigrationAmbiguity(
            f"{TARGET_WAREHOUSE_ENV}={raw!r} is not an integer warehouse id. "
            "Nothing was migrated."
        ) from None
    if not _table_exists(conn, "warehouses"):
        raise LegacyMigrationAmbiguity(
            f"{TARGET_WAREHOUSE_ENV}={wanted} was requested, but this DB has "
            "no 'warehouses' table at all. Restore it (or drop the variable) "
            "before migrating."
        )
    rows = _warehouse_rows(conn)
    if wanted not in {wid for wid, _n, _d in rows}:
        raise LegacyMigrationAmbiguity(
            f"{TARGET_WAREHOUSE_ENV}={wanted} does not exist in 'warehouses'. "
            "Known warehouses:\n" + (_format_warehouse_candidates(rows) or
                                     "    (table is empty)\n")
        )
    return wanted


def _resolve_target_warehouse_id(
    conn: sqlite3.Connection, *, log: Callable[[str], None] = print
) -> int | None:
    """Id of the warehouse legacy rows semantically belong to.

    The legacy schema has no warehouse concept at all: every row predates the
    split, so it belongs to the single warehouse the deployment actually has.

    Resolution order:

    1. ``LEGACY_MIGRATE_WAREHOUSE_ID`` when set — validated to exist first.
    2. the single row flagged ``is_default``.
    3. the single row, when the table has exactly one.

    Anything else is ambiguous and raises rather than picking arbitrarily.
    The old ``ORDER BY is_default DESC, id ASC LIMIT 1`` silently took *a* row
    when several carried ``is_default = 1``; which warehouse the entire legacy
    stock lands in is not a tie-break we get to make.

    Returns None only when there is no ``warehouses`` table or it is empty —
    the caller turns that into a refusal if any row still needs backfilling.
    """
    explicit = _explicit_target_warehouse_id(conn)
    if explicit is not None:
        log(f"[ok] {TARGET_WAREHOUSE_ENV}={explicit} — backfilling to it")
        return explicit

    if not _table_exists(conn, "warehouses"):
        return None
    rows = _warehouse_rows(conn)
    if not rows:
        return None

    defaults = [r for r in rows if r[2]]
    if len(defaults) == 1:
        return defaults[0][0]
    if len(defaults) > 1:
        raise LegacyMigrationAmbiguity(
            f"Refusing to migrate: {len(defaults)} warehouses are flagged "
            "is_default=1, so which one the legacy stock belongs to is "
            "undecidable:\n"
            + _format_warehouse_candidates(defaults)
            + f"Pick one explicitly with {TARGET_WAREHOUSE_ENV}=<id> (or fix "
            "the is_default flags), then re-run. Nothing was written."
        )
    if len(rows) == 1:
        return rows[0][0]
    raise LegacyMigrationAmbiguity(
        f"Refusing to migrate: 'warehouses' holds {len(rows)} rows and none "
        "is flagged is_default, so which one the legacy stock belongs to is "
        "undecidable:\n"
        + _format_warehouse_candidates(rows)
        + f"Pick one explicitly with {TARGET_WAREHOUSE_ENV}=<id> (or set "
        "is_default on the right row), then re-run. Nothing was written."
    )


def _tables_with_null_warehouse_id(
    conn: sqlite3.Connection, tables: list[str]
) -> list[tuple[str, int]]:
    """``(table, row_count)`` for tables still carrying NULL ``warehouse_id``."""
    pending: list[tuple[str, int]] = []
    for table in tables:
        if not _table_exists(conn, table):
            continue
        if "warehouse_id" not in _table_cols(conn, table):
            continue
        (n,) = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE warehouse_id IS NULL'
        ).fetchone()
        if n:
            pending.append((table, int(n)))
    return pending


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


def _has_table_options(sql: str) -> bool:
    """True when ``CREATE TABLE`` carries ``WITHOUT ROWID`` / ``STRICT``.

    Those keywords sit *after* the body's closing paren, so the "append the
    named constraint just before the final ``)``" rewrite below would splice
    the constraint into the wrong place (or fail to match at all). Everything
    after the last ``)`` is the table-options tail, so this is an exact test
    rather than a heuristic."""
    tail_start = sql.rfind(")")
    if tail_start < 0:
        return True  # unparseable — treat as unsafe
    tail = sql[tail_start + 1:].upper()
    return "WITHOUT" in tail or "STRICT" in tail


def _rebuild_with_named_unique(
    conn: sqlite3.Connection,
    table: str,
    col: str,
    constraint: str,
    *,
    log: Callable[[str], None] = print,
) -> bool:
    """Rebuild ``table`` so its UNIQUE on ``col`` is named ``constraint``.

    Reads the original ``CREATE TABLE`` SQL from ``sqlite_master``, strips the
    inline ``UNIQUE`` from ``col``'s column definition, and appends a named
    constraint. Data preserved via ``INSERT … SELECT *``.

    The rewrite is regex-based, not SQL-aware, so it is bracketed by checks
    rather than trusted: both substitutions must fire, tables with
    ``WITHOUT ROWID`` / ``STRICT`` options are skipped outright, and the
    rebuilt table is verified to have the same column set, the same row count
    and the requested constraint name. A failed post-check raises so the
    caller's transaction rolls the whole migration back — silently continuing
    with a mangled table is the one outcome worse than not starting.

    Returns True when the table was actually rebuilt."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row:
        return False
    original_sql = row[0]

    if _has_table_options(original_sql):
        log(
            f"[warn] {table}: CREATE TABLE carries WITHOUT ROWID / STRICT "
            "options — skipping the named-UNIQUE rebuild (the rewrite is not "
            "SQL-aware enough to place a constraint safely). If a later "
            "migration drops this constraint by name it will need doing by "
            "hand."
        )
        return False

    cols_before = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    (rows_before,) = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()

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
    new_sql, n_append = re.subn(
        r"\)\s*$",
        f", CONSTRAINT {constraint} UNIQUE ({col}))",
        new_sql.strip(),
        count=1,
    )
    if n_append == 0:
        # No recognisable trailing `)` to splice into. Bail out rather than
        # execute SQL we didn't actually transform.
        log(
            f"[warn] {table}: could not locate the closing paren of "
            "CREATE TABLE — skipping the named-UNIQUE rebuild."
        )
        return False

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

    # Post-checks. The regex above is not SQL-aware; a string DEFAULT
    # containing the word UNIQUE, an `ON CONFLICT` clause or a table-level
    # `UNIQUE(col)` can make it cut in the wrong place, and the damage would
    # otherwise only surface as missing columns or lost rows much later.
    cols_after = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if cols_after != cols_before:
        raise RuntimeError(
            f"{table}: rebuild for CONSTRAINT {constraint} changed the column "
            f"set ({cols_before} -> {cols_after}). The CREATE TABLE rewrite "
            "was wrong; aborting so the transaction rolls back."
        )
    (rows_after,) = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    if rows_after != rows_before:
        raise RuntimeError(
            f"{table}: rebuild for CONSTRAINT {constraint} changed the row "
            f"count ({rows_before} -> {rows_after}). Aborting so the "
            "transaction rolls back."
        )
    check = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not check or constraint.lower() not in (check[0] or "").lower():
        raise RuntimeError(
            f"{table}: rebuild completed but CONSTRAINT {constraint} is "
            "absent from the resulting CREATE TABLE. Aborting so the "
            "transaction rolls back."
        )
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


def find_batch_quantity_divergence(
    db_path: Path | str,
) -> list[tuple[int, str, float, float]]:
    """Materials whose cached ``quantity`` disagrees with their active batches.

    Read-only. Returns ``(material_id, name, cached_quantity, active_batch_sum)``
    tuples, empty when the DB has no batch history at all (``batches`` empty)
    or everything reconciles.

    An empty ``batches`` table is *not* a divergence: the deployment simply
    never used batches, and revision ``d6e7f8a9b0c1`` synthesising
    ``LEGACY-MIG-d6e7-*`` batches from ``materials.quantity`` is exactly that
    migration's intended semantics. Divergence on a DB that *does* have batch
    history is a different animal — revision ``6fec76bb57d9`` already notes it
    "needs manual investigation" — and letting d6e7f8a9b0c1 top it up would
    invent stock that never existed.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if not _table_exists(conn, "materials") or not _table_exists(conn, "batches"):
            return []
        m_cols = _table_cols(conn, "materials")
        b_cols = _table_cols(conn, "batches")
        if "quantity" not in m_cols or not {
            "material_id", "quantity", "is_exhausted"
        } <= b_cols:
            return []
        (n_batches,) = conn.execute("SELECT COUNT(*) FROM batches").fetchone()
        if not n_batches:
            return []
        name_col = "name" if "name" in m_cols else "id"
        rows = conn.execute(
            f"""
            SELECT m.id, m.{name_col}, m.quantity, COALESCE(b.active_sum, 0)
            FROM materials m
            LEFT JOIN (
                SELECT material_id, SUM(quantity) AS active_sum
                FROM batches WHERE is_exhausted = 0 GROUP BY material_id
            ) b ON b.material_id = m.id
            WHERE COALESCE(m.quantity, 0) != COALESCE(b.active_sum, 0)
            ORDER BY m.id
            """
        ).fetchall()
        return [(r[0], str(r[1]), r[2], r[3]) for r in rows]
    finally:
        conn.close()


def assert_auto_migration_unambiguous(
    db_path: Path | str,
    *,
    log: Callable[[str], None] = print,
) -> None:
    """Raise :class:`LegacyMigrationAmbiguity` if this DB must not auto-migrate.

    Read-only and side-effect free — safe to call before taking a backup.

    Bypassed only by an explicit ``ALLOW_AMBIGUOUS_BATCH_MIGRATION`` opt-in;
    the waiver is logged loudly because it is the operator asserting the books
    were reconciled by hand.
    """
    diverged = find_batch_quantity_divergence(db_path)
    if not diverged:
        return
    if allow_ambiguous_batch_migration():
        log(
            f"[warn] {ALLOW_AMBIGUOUS_ENV} is set: proceeding despite "
            f"{len(diverged)} material(s) whose cached quantity disagrees with "
            f"their active batches. Revision {BATCH_SYNTHESIS_REVISION} will "
            "synthesise LEGACY-MIG-d6e7-* batches for the positive "
            "differences."
        )
        return
    examples = "".join(
        f"    - material id={mid} {name!r}: materials.quantity={qty}, "
        f"active batch sum={bsum}\n"
        for mid, name, qty, bsum in diverged[:5]
    )
    more = (
        f"    ... and {len(diverged) - 5} more\n" if len(diverged) > 5 else ""
    )
    raise LegacyMigrationAmbiguity(
        f"Refusing to auto-migrate {db_path}: this DB has batch history, but "
        f"{len(diverged)} material(s) have a cached quantity that disagrees "
        "with the sum of their active batches:\n"
        f"{examples}{more}"
        "Migration d6e7f8a9b0c1 would 'fix' each of these by synthesising a "
        "LEGACY-MIG-d6e7-* batch for the positive difference — inventing "
        "stock and corrupting the existing ledger. Which number is right is a "
        "business question this tool must not guess (revision 6fec76bb57d9 "
        "flags the same divergence as needing manual investigation).\n"
        "Nothing was written; the DB is untouched. Reconcile the quantities "
        "by hand, then migrate explicitly:\n"
        "  uv run python scripts/migrate_legacy_db.py /path/to/warehouse.db\n"
        "or, inside the container (WORKDIR /app):\n"
        "  /app/.venv/bin/python -m backend.legacy_db_migration "
        "/data/warehouse.db\n"
        f"If the divergence has already been reviewed and is acceptable, set "
        f"{ALLOW_AMBIGUOUS_ENV}=1 to waive this gate."
    )


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

    # Ambiguity gate lives *inside* migrate(), before the backup and before any
    # write, so every entry point is covered by construction: the startup hook,
    # ``scripts/migrate_legacy_db.py`` and ``python -m
    # backend.legacy_db_migration`` all funnel through here. Putting it only in
    # the caller left the two CLI paths able to walk straight past it.
    assert_auto_migration_unambiguous(db_path, log=log)

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
        default_wh = _resolve_target_warehouse_id(conn, log=log)
        wh_tables = [
            t for t, patches in LEGACY_TABLE_PATCHES.items()
            if any(c == "warehouse_id" for c, _ in patches)
        ]
        if default_wh is None:
            # The seed step above already tried to create a default warehouse.
            # Still nothing to point at means the table is missing or could not
            # be seeded. Carrying on would leave warehouse_id NULL on every
            # row, and NULL matches neither ``warehouse_id = <id>`` nor
            # ``warehouse_id IN (...)`` in backend/deps.py -- the data would be
            # in the DB but invisible in the UI. That is exactly the bug this
            # backfill exists to prevent, so refuse instead of degrading.
            pending = _tables_with_null_warehouse_id(conn, wh_tables)
            if pending:
                raise LegacyMigrationAmbiguity(
                    "Refusing to migrate: no default warehouse could be "
                    "determined ('warehouses' is missing or empty and seeding "
                    "it did not work), but "
                    + ", ".join(f"{t} ({n} row(s))" for t, n in pending)
                    + " still need warehouse_id filled in. Leaving them NULL "
                    "would hide the data from every warehouse-scoped query in "
                    "backend/deps.py.\nCreate the warehouse row (or point at "
                    f"an existing one with {TARGET_WAREHOUSE_ENV}=<id>), then "
                    "re-run. Nothing was written."
                )
            log("[ok] no warehouse_id backfill needed")
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

        # Preserve ``inventory_records.reason`` before the alembic chain
        # deletes it. Revision c5d6e7f8a9b0 does an unconditional
        # ``drop_column('reason')`` with no data migration -- correct for a DB
        # that is already on a recent version (nothing has read the column for
        # a while), destructive for a genuinely old one where ``reason`` was
        # the only place the in/out reason was ever recorded. Copy the text
        # into ``reason_note`` while it still exists.
        #
        # ``reason_note`` wins when it already holds something: it is the newer
        # field and the user's most recent intent.
        if _table_exists(conn, "inventory_records"):
            ir_cols = _table_cols(conn, "inventory_records")
            if "reason" in ir_cols and "reason_note" in ir_cols:
                cur = conn.execute(
                    "UPDATE inventory_records SET reason_note = reason "
                    "WHERE reason IS NOT NULL AND TRIM(reason) != '' "
                    "AND (reason_note IS NULL OR TRIM(reason_note) = '')"
                )
                if cur.rowcount > 0:
                    changes.append(
                        f"inventory_records: preserved reason -> reason_note "
                        f"for {cur.rowcount} row(s)"
                    )
                    log(
                        f"[ok] inventory_records: preserved reason -> "
                        f"reason_note for {cur.rowcount} row(s)"
                    )

        for table, col, constraint in NAMED_UNIQUE_TARGETS:
            if not _table_exists(conn, table):
                continue
            if not _anonymous_unique_on(conn, table, col):
                continue
            if not _rebuild_with_named_unique(
                conn, table, col, constraint, log=log
            ):
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
    except LegacyMigrationAmbiguity as exc:
        # Same refusal the startup hook gives, minus the traceback: this is an
        # expected outcome that needs a human decision, not a crash.
        sys.exit(f"REFUSED: {exc}")


if __name__ == "__main__":
    main()
