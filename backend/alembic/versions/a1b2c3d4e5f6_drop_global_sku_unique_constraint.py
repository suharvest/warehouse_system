"""drop global unique constraints: materials.sku, warehouses.slug, batches.batch_no

Revision ID: a1b2c3d4e5f6
Revises: f8a9b0c1d2e3
Create Date: 2026-05-13 18:30:00.000000

SKU/slug/batch_no uniqueness must be scoped to warehouse or tenant, not global.
The initial schema incorrectly used single-column UNIQUE constraints which break
multi-tenant scenarios where different tenants use the same codes.

Correct constraints:
  - materials: UNIQUE(sku, warehouse_id)   [was: UNIQUE(sku)]
  - warehouses: UNIQUE(slug, tenant_id)    [was: UNIQUE(slug)]
  - batches:    UNIQUE(batch_no, warehouse_id) [was: UNIQUE(batch_no)]
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = 'a1b2c3d4e5f6'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def _index_exists(bind, table_name, index_name):
    insp = sa_inspect(bind)
    return any(idx['name'] == index_name for idx in insp.get_indexes(table_name))


def upgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == 'sqlite'

    # ── materials: drop global UNIQUE(sku), keep UNIQUE(sku, warehouse_id) ──
    with op.batch_alter_table('materials', recreate='always' if is_sqlite else 'never') as batch_op:
        batch_op.drop_constraint('uq_materials_sku', type_='unique')
    # Ensure the per-warehouse composite index exists (may already exist from initial migration)
    if not _index_exists(bind, 'materials', 'idx_materials_sku_wh'):
        with op.batch_alter_table('materials') as batch_op:
            batch_op.create_index('idx_materials_sku_wh', ['sku', 'warehouse_id'], unique=True)

    # ── warehouses: drop global UNIQUE(slug), add UNIQUE(slug, tenant_id) ──
    with op.batch_alter_table('warehouses', recreate='always' if is_sqlite else 'never') as batch_op:
        batch_op.drop_constraint('uq_warehouses_slug', type_='unique')
    if not _index_exists(bind, 'warehouses', 'idx_warehouses_slug_tenant'):
        with op.batch_alter_table('warehouses') as batch_op:
            batch_op.create_index('idx_warehouses_slug_tenant', ['slug', 'tenant_id'], unique=True)

    # ── batches: drop global UNIQUE(batch_no), add UNIQUE(batch_no, warehouse_id) ──
    with op.batch_alter_table('batches', recreate='always' if is_sqlite else 'never') as batch_op:
        batch_op.drop_constraint('uq_batches_batch_no', type_='unique')
    if not _index_exists(bind, 'batches', 'idx_batches_no_wh'):
        with op.batch_alter_table('batches') as batch_op:
            batch_op.create_index('idx_batches_no_wh', ['batch_no', 'warehouse_id'], unique=True)


def downgrade():
    with op.batch_alter_table('batches') as batch_op:
        try:
            batch_op.drop_index('idx_batches_no_wh')
        except Exception:
            pass
        batch_op.create_unique_constraint('uq_batches_batch_no', ['batch_no'])

    with op.batch_alter_table('warehouses') as batch_op:
        try:
            batch_op.drop_index('idx_warehouses_slug_tenant')
        except Exception:
            pass
        batch_op.create_unique_constraint('uq_warehouses_slug', ['slug'])

    with op.batch_alter_table('materials') as batch_op:
        batch_op.create_unique_constraint('uq_materials_sku', ['sku'])
