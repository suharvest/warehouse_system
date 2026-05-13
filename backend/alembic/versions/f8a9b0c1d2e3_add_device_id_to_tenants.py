"""add device_id to tenants

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-05-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f8a9b0c1d2e3'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tenants') as batch_op:
        batch_op.add_column(
            sa.Column('device_id', sa.String(255), nullable=True)
        )
        batch_op.create_unique_constraint('uq_tenants_device_id', ['device_id'])


def downgrade():
    with op.batch_alter_table('tenants') as batch_op:
        batch_op.drop_constraint('uq_tenants_device_id', type_='unique')
        batch_op.drop_column('device_id')
