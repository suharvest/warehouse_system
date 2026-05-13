"""add debug_mode to mcp_connections

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-05-13 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'e7f8a9b0c1d2'
down_revision = 'd6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mcp_connections') as batch_op:
        batch_op.add_column(
            sa.Column('debug_mode', sa.Integer(), server_default='0', nullable=False)
        )


def downgrade():
    with op.batch_alter_table('mcp_connections') as batch_op:
        batch_op.drop_column('debug_mode')
