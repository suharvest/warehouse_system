"""add device_id to mcp_connections

Revision ID: g6h7i8j9k0l1
Revises: e5f6g7h8i9j0
Create Date: 2026-05-15 14:00:00.000000

补 commit 88ed88d 漏写的迁移：metadata.py 中 mcp_connections 表
新增了 device_id 列（unique），但没生成对应 Alembic 迁移，
导致已经升级到 e5f6g7h8i9j0 的现网/本地 SQLite 库查询 mcp_connections
列表时 500：no such column: mcp_connections.device_id。

幂等性：legacy `init_database()` 在 backend/database.py:563-575 / 683-686
也会建 device_id 列 + idx_mcp_connections_device_id 索引。本迁移按
"列是否存在 / 唯一约束或唯一索引是否存在"做条件 add，兼容三种 db 状态：
  1) 完全从 Alembic 迁移建的库 — add_column + create_unique_constraint
  2) Legacy init_database 跑过的库 — 跳过 add_column，跳过 unique（已有 idx）
  3) 部分升级（列存在但没 unique） — 只补 unique constraint
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'g6h7i8j9k0l1'
down_revision = 'e5f6g7h8i9j0'
branch_labels = None
depends_on = None


def _column_exists(inspector, table: str, column: str) -> bool:
    return any(c['name'] == column for c in inspector.get_columns(table))


def _unique_exists(inspector, table: str, column: str) -> bool:
    """同时检查 UNIQUE CONSTRAINT 和 UNIQUE INDEX 两种形式。

    legacy init_database 走的是 'CREATE UNIQUE INDEX idx_mcp_connections_device_id'，
    Alembic create_unique_constraint 走的是 'uq_mcp_connections_device_id'，
    任一存在都视为唯一性已生效。
    """
    for ix in inspector.get_indexes(table):
        if ix.get('unique') and column in (ix.get('column_names') or []):
            return True
    for uc in inspector.get_unique_constraints(table):
        if column in (uc.get('column_names') or []):
            return True
    return False


def upgrade():
    bind = op.get_bind()
    # offline (--sql) 模式下 bind 是 MockConnection，无法 introspect；按全新库发完整 DDL。
    if context.is_offline_mode():
        has_column = has_unique = False
    else:
        inspector = inspect(bind)
        has_column = _column_exists(inspector, 'mcp_connections', 'device_id')
        has_unique = _unique_exists(inspector, 'mcp_connections', 'device_id') if has_column else False

    if has_column and has_unique:
        return  # legacy 库已完整，幂等跳过

    with op.batch_alter_table('mcp_connections') as batch_op:
        if not has_column:
            batch_op.add_column(
                sa.Column('device_id', sa.String(64), nullable=True)
            )
        if not has_unique:
            batch_op.create_unique_constraint(
                'uq_mcp_connections_device_id', ['device_id']
            )


def downgrade():
    """非对称 downgrade：只清掉本 revision 命名的 unique constraint，不删 device_id 列。

    本 migration upgrade 在 legacy-path db 上是 no-op（列和唯一索引由
    backend/database.py:563-575 / 683-686 创建），因此 downgrade 不能假设
    "列是本 revision 创建的"——盲 drop 会破坏 legacy 库结构。
    Alembic 社区通用做法：data-bearing 列宁可留无害遗物也不误删。
    下一次 upgrade 检测列已存在会自动跳过 add_column，幂等性不受影响。
    """
    bind = op.get_bind()
    inspector = inspect(bind)

    # 仅 drop Alembic 命名的 constraint（legacy 用 idx_mcp_connections_device_id 索引，
    # 名字不同，不会被误删）
    for uc in inspector.get_unique_constraints('mcp_connections'):
        if uc.get('name') == 'uq_mcp_connections_device_id':
            with op.batch_alter_table('mcp_connections') as batch_op:
                batch_op.drop_constraint('uq_mcp_connections_device_id', type_='unique')
            break
