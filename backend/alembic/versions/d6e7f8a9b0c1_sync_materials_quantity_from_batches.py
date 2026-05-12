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

Dialect-portable: works on both SQLite (local dev) and MySQL (cloud) via the
SQLAlchemy Core update with a correlated subquery.

Downgrade is a no-op — the divergence we are healing is exactly the bug; we
do not want to restore it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Set materials.quantity = SUM(active batches.quantity) for every material.

    Offline (``--sql``) mode emits the equivalent UPDATE statement.
    """
    if context.is_offline_mode():
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
