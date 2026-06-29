"""add greeting_enabled to tenant_face_config

Revision ID: h7i8j9k0l1m2
Revises: g6h7i8j9k0l1
Create Date: 2026-06-05 09:00:00.000000

补 metadata.py 中 tenant_face_config 新增的 greeting_enabled 列（被动迎宾
开关，xiaozhi 在设备连接 / 语音同步时读取并对齐设备本地被动识别状态）漏写
的迁移。没有本迁移时，全新 DB 跑 `alembic upgrade head` 后启动会被
app.py 的 schema 校验器（metadata vs db 不一致）硬拒绝启动。

幂等性：部分本地/现网 SQLite 库可能已手动 ALTER 加过该列（开发期直接改
metadata.py 试跑所致），故按"列是否存在"做条件 add，兼容两种 db 状态：
  1) 未加过列的库 — add_column
  2) 已有该列的库 — 跳过（no-op）
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'h7i8j9k0l1m2'
down_revision = 'g6h7i8j9k0l1'
branch_labels = None
depends_on = None


def _column_exists(inspector, table: str, column: str) -> bool:
    return any(c['name'] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    # offline (--sql) 模式下 bind 是 MockConnection，无法 introspect；跳过幂等检查直接发 DDL。
    if not context.is_offline_mode():
        inspector = inspect(bind)
        if _column_exists(inspector, 'tenant_face_config', 'greeting_enabled'):
            return  # 列已存在，幂等跳过

    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.add_column(
            sa.Column(
                'greeting_enabled',
                sa.Boolean(),
                nullable=False,
                server_default='0',
            )
        )


def downgrade():
    """data-bearing 列：仅在列存在时 drop，避免对未加过列的库报错。"""
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _column_exists(inspector, 'tenant_face_config', 'greeting_enabled'):
        return

    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.drop_column('greeting_enabled')
