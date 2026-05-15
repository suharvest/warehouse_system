"""add device_id to mcp_connections

Revision ID: g6h7i8j9k0l1
Revises: e5f6g7h8i9j0
Create Date: 2026-05-15 14:00:00.000000

补 commit 88ed88d 漏写的迁移：metadata.py 中 mcp_connections 表
新增了 device_id 列（unique），但没生成对应 Alembic 迁移，
导致已经升级到 e5f6g7h8i9j0 的现网/本地 SQLite 库查询 mcp_connections
列表时 500：no such column: mcp_connections.device_id。
"""
from alembic import op
import sqlalchemy as sa

revision = 'g6h7i8j9k0l1'
down_revision = 'e5f6g7h8i9j0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mcp_connections') as batch_op:
        batch_op.add_column(
            sa.Column('device_id', sa.String(64), nullable=True)
        )
        batch_op.create_unique_constraint(
            'uq_mcp_connections_device_id', ['device_id']
        )


def downgrade():
    with op.batch_alter_table('mcp_connections') as batch_op:
        batch_op.drop_constraint('uq_mcp_connections_device_id', type_='unique')
        batch_op.drop_column('device_id')
