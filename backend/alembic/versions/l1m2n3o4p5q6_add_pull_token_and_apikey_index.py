"""add mcp_agent_devices.pull_token + mcp_connections.api_key index

Revision ID: l1m2n3o4p5q6
Revises: k0l1m2n3o4p5
Create Date: 2026-07-16 10:00:00.000000

B 方案（后端直连设备拉取身份，消除 session 模式的提示注入面）需要：
- ``mcp_agent_devices.pull_token``：每设备独立、可轮换的鉴权 token，随人脸库
  下发到设备 NVS ``face.pull_token``，后端拉取时置于 ``X-Face-Token``。与远端识别的
  ``identify_token``（仅 lan 模式有值）刻意分离，避免复用扩大泄露面 / 本机模式无 token
  致端点永远 401。
- ``mcp_connections.api_key`` 索引：verify-mcp 每次按明文 api_key 反查连接以定位设备，
  加索引避免全表扫。

幂等：legacy ``init_database()``（backend/database.py）已同步加该列/索引（raw 兜底），
所以走 raw 路径建的库这里会发现已存在并跳过；走 Alembic 整链的存量库由本迁移补上。
``inspector`` guard 兼容两种状态。
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'l1m2n3o4p5q6'
down_revision = 'k0l1m2n3o4p5'
branch_labels = None
depends_on = None


def _columns(table: str):
    if context.is_offline_mode():
        return set()
    bind = op.get_bind()
    return {c['name'] for c in inspect(bind).get_columns(table)}


def _indexes(table: str):
    if context.is_offline_mode():
        return set()
    bind = op.get_bind()
    return {ix['name'] for ix in inspect(bind).get_indexes(table)}


def upgrade():
    if 'pull_token' not in _columns('mcp_agent_devices'):
        op.add_column('mcp_agent_devices',
                      sa.Column('pull_token', sa.String(64), nullable=True))
    if 'idx_mcp_connections_api_key' not in _indexes('mcp_connections'):
        op.create_index('idx_mcp_connections_api_key',
                        'mcp_connections', ['api_key'])


def downgrade():
    if 'idx_mcp_connections_api_key' in _indexes('mcp_connections'):
        op.drop_index('idx_mcp_connections_api_key', table_name='mcp_connections')
    if 'pull_token' in _columns('mcp_agent_devices'):
        op.drop_column('mcp_agent_devices', 'pull_token')
