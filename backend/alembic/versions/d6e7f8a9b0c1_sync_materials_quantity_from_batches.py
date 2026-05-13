"""sync materials.quantity from batches

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-05-12 10:00:00.000000

After this point ``batches.quantity`` is the single source of truth for
on-hand inventory (see ``database.get_material_quantity`` and the
``/api/materials/product-stats`` endpoint). The legacy ``materials.quantity``
column stays for compatibility (other code still reads/writes it) but it is a
derived cache. This migration brings the column into agreement with the
authoritative aggregate so that the cache is correct at cutover:

    UPDATE materials
    SET    quantity = (SELECT COALESCE(SUM(b.quantity), 0)
                       FROM batches b
                       WHERE b.material_id = materials.id
                         AND b.is_exhausted = 0)

But first we have to handle the **legacy-only** case: historical databases
have rows where ``materials.quantity > 0`` but **no active batch** exists
(prior to this refactor ``database.init_database`` would synthesise a
``LEGACY-XXXX`` batch from the divergence on startup; we removed that
synthesis but still have to cover the upgrade path). If we ran the UPDATE
above unconditionally those materials would be zeroed out, silently losing
inventory. So before the sync we materialise a compensating
``LEGACY-MIG-d6e7-<id>`` batch holding the missing stock for every such
material, then proceed with the cache sync.

Dialect-portable: works on both SQLite (local dev) and MySQL (cloud) via
SQLAlchemy Core constructs only — no dialect-specific SQL.

Downgrade is a no-op — the divergence we are healing is exactly the bug; we
do not want to restore it.
"""
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Marker used as batch_no prefix for the compensating rows this migration
# creates. Kept distinct from the historical "LEGACY-XXXX" prefix used by the
# (now removed) init_database synthesis path so we cannot collide with an
# existing row regardless of how the DB was bootstrapped.
LEGACY_PREFIX = 'LEGACY-MIG-d6e7-'


def upgrade() -> None:
    """Pre-create compensating batches for legacy-only stock, then sync the
    ``materials.quantity`` cache from the aggregate of active batches.

    Offline (``--sql``) mode emits the equivalent SQL.
    """
    if context.is_offline_mode():
        # Portable SQL fallback for ``alembic upgrade --sql`` review.
        # 1) Helper index. Use plain ``CREATE INDEX`` here; SQL output is for
        #    DBA review, not direct execution.
        op.execute(
            "CREATE INDEX ix_batches_mid_exh "
            "ON batches (material_id, is_exhausted)"
        )
        # 2) Compensate legacy-only stock: insert one batch row per material
        #    whose cached quantity exceeds its active-batch sum.
        op.execute(
            "INSERT INTO batches "
            "(batch_no, material_id, quantity, initial_quantity, location, "
            " created_at, is_exhausted, tenant_id, warehouse_id) "
            "SELECT "
            "  CONCAT('" + LEGACY_PREFIX + "', m.id), "
            "  m.id, "
            "  m.quantity - COALESCE(b.active_sum, 0), "
            "  m.quantity - COALESCE(b.active_sum, 0), "
            "  m.location, CURRENT_TIMESTAMP, 0, m.tenant_id, m.warehouse_id "
            "FROM materials m "
            "LEFT JOIN (SELECT material_id, SUM(quantity) AS active_sum "
            "           FROM batches WHERE is_exhausted = 0 "
            "           GROUP BY material_id) b ON b.material_id = m.id "
            "WHERE m.quantity > COALESCE(b.active_sum, 0)"
        )
        # 3) Materials cache sync.
        op.execute(
            "UPDATE materials SET quantity = ("
            "  SELECT COALESCE(SUM(b.quantity), 0) FROM batches b "
            "  WHERE b.material_id = materials.id AND b.is_exhausted = 0"
            ")"
        )
        return

    conn = op.get_bind()
    md = sa.MetaData()
    materials = sa.Table("materials", md, autoload_with=conn)
    batches = sa.Table("batches", md, autoload_with=conn)

    # 1) Helper index. Speeds up the correlated subquery used in step 3 on
    #    large MySQL tables. ``CREATE INDEX IF NOT EXISTS`` is not portable
    #    across MySQL versions, so we do an inspect-then-create dance.
    inspector = sa.inspect(conn)
    existing_idx = {ix["name"] for ix in inspector.get_indexes("batches")}
    if "ix_batches_mid_exh" not in existing_idx:
        op.create_index(
            "ix_batches_mid_exh", "batches",
            ["material_id", "is_exhausted"],
        )

    # 2) Compensate legacy-only stock — materials with cached quantity > 0
    #    but no (or insufficient) active batches. Without this the UPDATE in
    #    step 3 would zero them out.
    active_sum_subq = (
        sa.select(
            batches.c.material_id.label("material_id"),
            sa.func.sum(batches.c.quantity).label("active_sum"),
        )
        .where(batches.c.is_exhausted == 0)
        .group_by(batches.c.material_id)
        .subquery()
    )

    diff_expr = materials.c.quantity - sa.func.coalesce(
        active_sum_subq.c.active_sum, 0
    )

    select_legacy = (
        sa.select(
            (sa.literal(LEGACY_PREFIX) + sa.cast(materials.c.id, sa.String(32)))
                .label("batch_no"),
            materials.c.id.label("material_id"),
            diff_expr.label("quantity"),
            diff_expr.label("initial_quantity"),
            materials.c.location.label("location"),
            sa.literal(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
                .label("created_at"),
            sa.literal(0).label("is_exhausted"),
            materials.c.tenant_id.label("tenant_id"),
            materials.c.warehouse_id.label("warehouse_id"),
        )
        .select_from(
            materials.outerjoin(
                active_sum_subq,
                active_sum_subq.c.material_id == materials.c.id,
            )
        )
        .where(diff_expr > 0)
    )

    insert_legacy = batches.insert().from_select(
        [
            "batch_no", "material_id", "quantity", "initial_quantity",
            "location", "created_at", "is_exhausted", "tenant_id",
            "warehouse_id",
        ],
        select_legacy,
    )
    conn.execute(insert_legacy)

    # 3) Cache sync. After step 2 every previously legacy-only material has
    #    at least one active batch covering its historical stock, so this
    #    UPDATE will not zero anyone out.
    sub = (
        sa.select(sa.func.coalesce(sa.func.sum(batches.c.quantity), 0))
        .where(
            sa.and_(
                batches.c.material_id == materials.c.id,
                batches.c.is_exhausted == 0,
            )
        )
        .scalar_subquery()
    )
    conn.execute(sa.update(materials).values(quantity=sub))


def downgrade() -> None:
    """No-op: restoring the prior diverged state has no value (it was a bug)."""
    return
