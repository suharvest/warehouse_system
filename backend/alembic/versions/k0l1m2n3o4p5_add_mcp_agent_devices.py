"""add mcp_agent_devices (per-agent physical device subtable)

Revision ID: k0l1m2n3o4p5
Revises: j9k0l1m2n3o4
Create Date: 2026-06-30 09:00:00.000000

一对多子表：一个 mcp_connections 行（云端智能体端点）下可挂多个物理设备，
每行存设备的 LAN IP / NPU 模型标签等，供后续"云端下发人脸库到设备"使用。
connection_id ON DELETE CASCADE：智能体删了，设备记录跟着删。

幂等：legacy ``init_database()``（backend/database.py）也会建这张表，
且 initial_schema 迁移（1826e23835b6）的 create_table 已同步加表，所以
对全新走 Alembic 整链的库，本迁移会发现表已存在并跳过；对已经停在
head j9k0l1m2n3o4 的存量库（表尚不存在），本迁移负责补建。
``inspector.has_table`` guard 兼容这两种状态。
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'k0l1m2n3o4p5'
down_revision = 'j9k0l1m2n3o4'
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    # offline (--sql) 模式无法 introspect；按全新库发完整 DDL。
    if context.is_offline_mode():
        return False
    return inspect(op.get_bind()).has_table(table)


def upgrade():
    if _has_table('mcp_agent_devices'):
        return  # legacy init_database / initial_schema 已建表，幂等跳过

    op.create_table(
        'mcp_agent_devices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('connection_id', sa.String(length=64), nullable=False),
        sa.Column('device_id', sa.String(length=128), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('port', sa.Integer(), server_default='80', nullable=False),
        sa.Column('model_tag', sa.String(length=64), nullable=True),
        sa.Column('face_enabled', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_seen', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.String(length=32), nullable=True),
        sa.Column('updated_at', sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ['connection_id'], ['mcp_connections.id'],
            name=op.f('fk_mcp_agent_devices_connection_id_mcp_connections'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mcp_agent_devices')),
        sa.UniqueConstraint(
            'connection_id', 'device_id',
            name='uq_mcp_agent_devices_connection_id_device_id',
        ),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_0900_ai_ci',
    )
    with op.batch_alter_table('mcp_agent_devices', schema=None) as batch_op:
        batch_op.create_index(
            'idx_mcp_agent_devices_connection', ['connection_id'], unique=False
        )


def downgrade():
    # 仅在表存在时 drop；legacy 库可能由 init_database 建表，downgrade 一并清掉。
    if not _has_table('mcp_agent_devices'):
        return
    with op.batch_alter_table('mcp_agent_devices', schema=None) as batch_op:
        batch_op.drop_index('idx_mcp_agent_devices_connection')
    op.drop_table('mcp_agent_devices')
