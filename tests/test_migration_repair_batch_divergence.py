"""Tests for the data-repair migration ``6fec76bb57d9``.

We bootstrap a fresh sqlite DB by stamping it to the previous head
(``1826e23835b6`` — the initial schema migration), seed the bug pattern,
then exercise the repair migration's upgrade / downgrade body directly.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "6fec76bb57d9_repair_batch_divergence_and_orphan_out_.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "_repair_migration_6fec76bb57d9", MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def db_engine():
    """Spin up a fresh sqlite DB stamped at the initial-schema head."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    # Preserve any caller-supplied DATABASE_URL (e.g. MySQL CI runs) so
    # that subsequent tests still see the original engine. Restoring on
    # teardown matters: simply popping the var lets ``db.get_engine``
    # fall back to ``DATABASE_PATH``, which other fixtures may have left
    # pointing at a stale value.
    _prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    # Upgrade only to the initial schema, NOT to head — we run the repair
    # migration manually in each test so we control timing relative to seed.
    command.upgrade(cfg, "1826e23835b6")

    eng = create_engine(url, future=True)
    yield eng, cfg
    eng.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass
    if _prev_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = _prev_url


def _seed_min_refs(conn):
    """Insert tenant + warehouse + contact rows that FKs require."""
    md = sa.MetaData()
    tenants = sa.Table("tenants", md, autoload_with=conn)
    warehouses = sa.Table("warehouses", md, autoload_with=conn)
    conn.execute(
        sa.insert(tenants).values(id=1, slug="default", name="Default")
    )
    conn.execute(
        sa.insert(warehouses).values(
            id=1, slug="default", name="Default", is_default=1
        )
    )


def _insert_material(conn, *, mid, qty, name="m"):
    md = sa.MetaData()
    materials = sa.Table("materials", md, autoload_with=conn)
    conn.execute(
        sa.insert(materials).values(
            id=mid,
            name=name,
            sku=f"SKU-{mid}",
            category="cat",
            quantity=qty,
            unit="pcs",
            warehouse_id=1,
            tenant_id=1,
        )
    )


def _insert_batch(conn, *, bid, material_id, qty, created_at, exhausted=0):
    md = sa.MetaData()
    batches = sa.Table("batches", md, autoload_with=conn)
    conn.execute(
        sa.insert(batches).values(
            id=bid,
            batch_no=f"B-{bid}",
            material_id=material_id,
            quantity=qty,
            initial_quantity=qty,
            is_exhausted=exhausted,
            warehouse_id=1,
            created_at=created_at,
            tenant_id=1,
        )
    )


def _insert_out_record(conn, *, rid, material_id, qty, created_at):
    md = sa.MetaData()
    inv = sa.Table("inventory_records", md, autoload_with=conn)
    conn.execute(
        sa.insert(inv).values(
            id=rid,
            material_id=material_id,
            type="out",
            quantity=qty,
            warehouse_id=1,
            tenant_id=1,
            created_at=created_at,
        )
    )


def _run_upgrade(cfg):
    command.upgrade(cfg, "6fec76bb57d9")


def test_repair_excess_consumed_fifo(db_engine):
    eng, cfg = db_engine
    base = datetime(2026, 1, 1, 12, 0, 0)
    with eng.begin() as conn:
        _seed_min_refs(conn)
        _insert_material(conn, mid=1, qty=2)
        # Four batches summing to 5 (excess = 3 → consume oldest first).
        _insert_batch(conn, bid=10, material_id=1, qty=1, created_at=base)
        _insert_batch(conn, bid=11, material_id=1, qty=2, created_at=base + timedelta(minutes=1))
        _insert_batch(conn, bid=12, material_id=1, qty=1, created_at=base + timedelta(minutes=2))
        _insert_batch(conn, bid=13, material_id=1, qty=1, created_at=base + timedelta(minutes=3))

    _run_upgrade(cfg)

    with eng.connect() as conn:
        md = sa.MetaData()
        batches = sa.Table("batches", md, autoload_with=conn)
        rows = {
            r.id: (int(r.quantity), int(r.is_exhausted))
            for r in conn.execute(sa.select(batches.c.id, batches.c.quantity, batches.c.is_exhausted)).fetchall()
        }
    # Excess 3 consumed FIFO: bid 10 (1) + bid 11 (2) -> 0/exhausted.
    # bid 12, 13 untouched.
    assert rows[10] == (0, 1)
    assert rows[11] == (0, 1)
    assert rows[12] == (1, 0)
    assert rows[13] == (1, 0)
    # sum equals materials.quantity now
    assert sum(q for q, _ in rows.values()) == 2


def test_repair_orphan_consumption_backfilled(db_engine):
    eng, cfg = db_engine
    base = datetime(2026, 1, 1, 12, 0, 0)
    with eng.begin() as conn:
        _seed_min_refs(conn)
        _insert_material(conn, mid=1, qty=5)
        # Sum equals material.quantity (no divergence) but we have an
        # OUT record without batch_consumptions.
        _insert_batch(conn, bid=10, material_id=1, qty=5, created_at=base)
        _insert_out_record(
            conn, rid=100, material_id=1, qty=2,
            created_at=base + timedelta(hours=1),
        )

    _run_upgrade(cfg)

    with eng.connect() as conn:
        md = sa.MetaData()
        consumptions = sa.Table("batch_consumptions", md, autoload_with=conn)
        rows = conn.execute(
            sa.select(consumptions.c.record_id, consumptions.c.batch_id, consumptions.c.quantity)
            .where(consumptions.c.record_id == 100)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0].batch_id == 10
    assert rows[0].quantity == 2


def test_repair_reverse_divergence_logged_not_mutated(db_engine):
    eng, cfg = db_engine
    base = datetime(2026, 1, 1, 12, 0, 0)
    with eng.begin() as conn:
        _seed_min_refs(conn)
        _insert_material(conn, mid=1, qty=10)
        _insert_batch(conn, bid=10, material_id=1, qty=5, created_at=base)

    _run_upgrade(cfg)

    with eng.connect() as conn:
        md = sa.MetaData()
        materials = sa.Table("materials", md, autoload_with=conn)
        batches = sa.Table("batches", md, autoload_with=conn)
        log_tbl = sa.Table("repair_log", md, autoload_with=conn)
        mat_qty = conn.execute(sa.select(materials.c.quantity).where(materials.c.id == 1)).scalar_one()
        bqty = conn.execute(sa.select(batches.c.quantity).where(batches.c.id == 10)).scalar_one()
        warns = conn.execute(
            sa.select(log_tbl.c.material_id, log_tbl.c.action)
            .where(log_tbl.c.action == "reverse_divergence_warning")
        ).fetchall()
    assert mat_qty == 10
    assert bqty == 5
    assert any(w.material_id == 1 for w in warns)


def test_repair_idempotent(db_engine):
    eng, cfg = db_engine
    base = datetime(2026, 1, 1, 12, 0, 0)
    with eng.begin() as conn:
        _seed_min_refs(conn)
        _insert_material(conn, mid=1, qty=2)
        _insert_batch(conn, bid=10, material_id=1, qty=3, created_at=base)

    # Run upgrade once via Alembic.
    _run_upgrade(cfg)

    # Second run: directly call _do_repair on a connection. Alembic version
    # table prevents a real second migration run, but our internal guard is
    # what we're testing here.
    mod = _load_migration_module()
    with eng.begin() as conn:
        mod._do_repair(conn)  # should be a no-op (snapshot table exists)

    with eng.connect() as conn:
        md = sa.MetaData()
        batches = sa.Table("batches", md, autoload_with=conn)
        snap = sa.Table(mod.SNAP_MATERIALS, md, autoload_with=conn)
        bqty = conn.execute(sa.select(batches.c.quantity).where(batches.c.id == 10)).scalar_one()
        snap_count = conn.execute(sa.select(sa.func.count()).select_from(snap)).scalar_one()
    # batch was consumed once (3 -> 2), second run did not touch it again
    assert bqty == 2
    # snapshot table still has exactly the original 1 row from first run
    assert snap_count == 1


def test_downgrade_restores_snapshots(db_engine):
    eng, cfg = db_engine
    base = datetime(2026, 1, 1, 12, 0, 0)
    with eng.begin() as conn:
        _seed_min_refs(conn)
        _insert_material(conn, mid=1, qty=2)
        _insert_batch(conn, bid=10, material_id=1, qty=1, created_at=base)
        _insert_batch(conn, bid=11, material_id=1, qty=2, created_at=base + timedelta(minutes=1))
        # Orphan OUT to exercise backfill restore as well.
        _insert_out_record(
            conn, rid=100, material_id=1, qty=1,
            created_at=base + timedelta(hours=1),
        )

    _run_upgrade(cfg)

    # Confirm mutation happened
    with eng.connect() as conn:
        md = sa.MetaData()
        batches = sa.Table("batches", md, autoload_with=conn)
        consumptions = sa.Table("batch_consumptions", md, autoload_with=conn)
        bqty10 = conn.execute(sa.select(batches.c.quantity).where(batches.c.id == 10)).scalar_one()
        c_count = conn.execute(
            sa.select(sa.func.count()).select_from(consumptions)
            .where(consumptions.c.record_id == 100)
        ).scalar_one()
    assert bqty10 == 0  # consumed by FIFO repair
    assert c_count >= 1

    # Downgrade
    command.downgrade(cfg, "1826e23835b6")

    with eng.connect() as conn:
        insp = inspect(conn)
        mod = _load_migration_module()
        assert not insp.has_table(mod.SNAP_MATERIALS)
        assert not insp.has_table(mod.SNAP_BATCHES)
        assert not insp.has_table(mod.SNAP_CONSUMPTIONS)
        assert not insp.has_table(mod.LOG_TABLE)

        md = sa.MetaData()
        batches = sa.Table("batches", md, autoload_with=conn)
        materials = sa.Table("materials", md, autoload_with=conn)
        consumptions = sa.Table("batch_consumptions", md, autoload_with=conn)
        bqty10 = conn.execute(sa.select(batches.c.quantity).where(batches.c.id == 10)).scalar_one()
        bqty11 = conn.execute(sa.select(batches.c.quantity).where(batches.c.id == 11)).scalar_one()
        mqty = conn.execute(sa.select(materials.c.quantity).where(materials.c.id == 1)).scalar_one()
        c_count = conn.execute(
            sa.select(sa.func.count()).select_from(consumptions)
            .where(consumptions.c.record_id == 100)
        ).scalar_one()
    assert bqty10 == 1
    assert bqty11 == 2
    assert mqty == 2
    assert c_count == 0
