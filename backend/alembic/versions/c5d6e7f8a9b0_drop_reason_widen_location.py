"""drop inventory_records.reason and widen *.location to 512

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-05-11 16:25:00.000000

Two unrelated but small schema cleanups bundled together:

1. Drop ``inventory_records.reason``. Replaced by ``reason_category`` +
   ``reason_note`` since revision 1826e23835b6 (initial schema already
   has both new columns). The legacy backfill helper
   ``_migrate_reason_to_category`` in ``database.py`` has been the only
   reader of this column for a while and is itself a legacy SQLite-init
   path no longer invoked at startup. Source code references removed.

2. Widen ``materials.location`` and ``batches.location`` from
   VARCHAR(255) to VARCHAR(512). Real users use these fields as free-form
   notes ("二号架顶层 / 备件区 / 注意防潮"), and 255 has been cutting it
   close. Not promoting to TEXT — these stay searchable/indexable.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('inventory_records') as batch_op:
        batch_op.drop_column('reason')

    with op.batch_alter_table('materials') as batch_op:
        batch_op.alter_column(
            'location',
            existing_type=sa.String(length=255),
            type_=sa.String(length=512),
            existing_nullable=True,
        )

    with op.batch_alter_table('batches') as batch_op:
        batch_op.alter_column(
            'location',
            existing_type=sa.String(length=255),
            type_=sa.String(length=512),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table('batches') as batch_op:
        batch_op.alter_column(
            'location',
            existing_type=sa.String(length=512),
            type_=sa.String(length=255),
            existing_nullable=True,
        )

    with op.batch_alter_table('materials') as batch_op:
        batch_op.alter_column(
            'location',
            existing_type=sa.String(length=512),
            type_=sa.String(length=255),
            existing_nullable=True,
        )

    # 下回滚后字段为 NULL — 已无来源还原原文本。
    with op.batch_alter_table('inventory_records') as batch_op:
        batch_op.add_column(sa.Column('reason', sa.String(length=255), nullable=True))
