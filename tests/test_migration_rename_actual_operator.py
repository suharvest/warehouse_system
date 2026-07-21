"""Tests for the rename migration ``p5q6r7s8t9u0``.

Covers both:
  * a brand-new sqlite DB upgraded to ``head`` ends up with
    ``inventory_records.actual_operator`` (and NOT ``operator_face_name``);
  * a DB stamped at the prior revision ``o4p5q6r7s8t9`` (which still has an
    ``operator_face_name`` column) is upgraded to ``head`` — the column is
    renamed to ``actual_operator`` AND any value it held survives.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

PREV_REV = "o4p5q6r7s8t9"


@pytest.fixture()
def alembic_cfg():
    """Fresh sqlite DB + alembic config; DATABASE_URL restored on teardown."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    _prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    yield cfg, url

    try:
        os.unlink(path)
    except OSError:
        pass
    if _prev_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = _prev_url


def _record_columns(url: str):
    eng = create_engine(url, future=True)
    try:
        insp = inspect(eng)
        return {c["name"] for c in insp.get_columns("inventory_records")}
    finally:
        eng.dispose()


def test_fresh_db_upgrade_head_has_actual_operator(alembic_cfg):
    cfg, url = alembic_cfg
    command.upgrade(cfg, "head")
    cols = _record_columns(url)
    assert "actual_operator" in cols
    assert "operator_face_name" not in cols


def test_rename_preserves_value_from_prev_revision(alembic_cfg):
    cfg, url = alembic_cfg
    # Stamp only up to the revision that still uses operator_face_name.
    command.upgrade(cfg, PREV_REV)

    cols = _record_columns(url)
    assert "operator_face_name" in cols
    assert "actual_operator" not in cols

    # Seed minimal refs + a record carrying a face-name value.
    eng = create_engine(url, future=True)
    try:
        with eng.begin() as conn:
            md = sa.MetaData()
            tenants = sa.Table("tenants", md, autoload_with=conn)
            warehouses = sa.Table("warehouses", md, autoload_with=conn)
            materials = sa.Table("materials", md, autoload_with=conn)
            inv = sa.Table("inventory_records", md, autoload_with=conn)
            conn.execute(sa.insert(tenants).values(id=1, slug="default", name="Default"))
            conn.execute(sa.insert(warehouses).values(
                id=1, slug="default", name="Default", is_default=1))
            conn.execute(sa.insert(materials).values(
                id=1, name="m", sku="SKU-1", category="cat", quantity=5,
                unit="pcs", warehouse_id=1, tenant_id=1))
            conn.execute(sa.insert(inv).values(
                id=100, material_id=1, type="in", quantity=5,
                operator="seeed", operator_face_name="张三",
                warehouse_id=1, tenant_id=1))
    finally:
        eng.dispose()

    # Upgrade to head → rename happens.
    command.upgrade(cfg, "head")

    cols = _record_columns(url)
    assert "actual_operator" in cols
    assert "operator_face_name" not in cols

    eng = create_engine(url, future=True)
    try:
        with eng.connect() as conn:
            md = sa.MetaData()
            inv = sa.Table("inventory_records", md, autoload_with=conn)
            val = conn.execute(
                sa.select(inv.c.actual_operator).where(inv.c.id == 100)
            ).scalar_one()
    finally:
        eng.dispose()
    assert val == "张三"  # value survived the rename
