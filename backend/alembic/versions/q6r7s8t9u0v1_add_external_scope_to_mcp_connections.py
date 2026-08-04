"""add external_tenant_id / external_warehouse_id to mcp_connections

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u0
Create Date: 2026-08-04 08:10:00.000000

外部 ERP 模式下，智能体需要绑定**对方系统**里的租户与仓库。我们的
warehouses / tenants 跟对方的没有任何对应关系，在本地镜像一套只会带来双重
维护和必然的数据漂移；所以改成由 Provider 探测（list_tenants /
list_warehouses），用户在配置智能体时直接选，这里只存选中的原始编码并原样
透传给对方。

两列都可空：
  - 自有模式：两列为空，继续用 warehouse_id（Integer FK）做作用域
  - 外部模式：用这两列，warehouse_id 可以为空

幂等性：按"列是否存在"做条件 add，兼容开发期已手动 ALTER 过的库
（database.py 的 init_database 也有同名 ALTER 兜底路径）。
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'q6r7s8t9u0v1'
down_revision = 'p5q6r7s8t9u0'
branch_labels = None
depends_on = None

_COLUMNS = ('external_tenant_id', 'external_warehouse_id')


def _column_exists(inspector, table: str, column: str) -> bool:
    return any(c['name'] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    # offline (--sql) 模式下 bind 是 MockConnection，无法 introspect；
    # 跳过幂等检查直接发 DDL。
    existing = set()
    if not context.is_offline_mode():
        inspector = inspect(bind)
        existing = {
            c for c in _COLUMNS
            if _column_exists(inspector, 'mcp_connections', c)
        }
        if len(existing) == len(_COLUMNS):
            return  # 两列都在，幂等跳过

    with op.batch_alter_table('mcp_connections') as batch_op:
        for col in _COLUMNS:
            if col not in existing:
                batch_op.add_column(sa.Column(col, sa.String(128), nullable=True))


def downgrade():
    """data-bearing 列：仅在列存在时 drop，避免对未加过列的库报错。"""
    bind = op.get_bind()
    inspector = inspect(bind)

    present = [
        c for c in _COLUMNS
        if _column_exists(inspector, 'mcp_connections', c)
    ]
    if not present:
        return

    with op.batch_alter_table('mcp_connections') as batch_op:
        for col in present:
            batch_op.drop_column(col)
