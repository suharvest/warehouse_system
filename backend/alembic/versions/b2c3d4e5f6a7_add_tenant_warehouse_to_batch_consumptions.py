"""add tenant_id and warehouse_id to batch_consumptions

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('batch_consumptions') as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('warehouse_id', sa.Integer(), nullable=True))
    # Backfill from parent batches table
    op.execute("""
        UPDATE batch_consumptions
        SET tenant_id = (SELECT tenant_id FROM batches WHERE batches.id = batch_consumptions.batch_id),
            warehouse_id = (SELECT warehouse_id FROM batches WHERE batches.id = batch_consumptions.batch_id)
    """)
    with op.batch_alter_table('batch_consumptions') as batch_op:
        batch_op.create_index('idx_bc_tenant', ['tenant_id'], unique=False)
        batch_op.create_index('idx_bc_warehouse', ['warehouse_id'], unique=False)


def downgrade():
    with op.batch_alter_table('batch_consumptions') as batch_op:
        batch_op.drop_index('idx_bc_warehouse')
        batch_op.drop_index('idx_bc_tenant')
        batch_op.drop_column('warehouse_id')
        batch_op.drop_column('tenant_id')
