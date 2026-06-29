"""add verify_mode to tenant_face_config

Revision ID: i8j9k0l1m2n3
Revises: h7i8j9k0l1m2
Create Date: 2026-06-29 10:00:00.000000

补 metadata.py 中 tenant_face_config 新增的 verify_mode 列（鉴权强度，与
推理拓扑 mode 正交）：
  - interface — warehouse 重比对 embedding，fail-closed（默认，保留存量行为，
                存量租户不被悄悄降级）
  - session   — 信任设备本地匹配，记 advisory 日志放行，不重比对

没有本迁移时，全新 DB 跑 `alembic upgrade head` 后启动会被 app.py 的 schema
校验器（metadata vs db 不一致）硬拒绝启动（见 memory
`project_alembic_chain_orphan_migration`）。

幂等性：initial_schema 与部分本地库可能已带该列（开发期直接改 metadata.py
试跑、或全新库走 initial_schema 已建列），故按"列是否存在"做条件 add，兼容
两种 db 状态：
  1) 未加过列的库 — add_column + check constraint
  2) 已有该列的库 — 跳过（no-op）
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'i8j9k0l1m2n3'
down_revision = 'h7i8j9k0l1m2'
branch_labels = None
depends_on = None


def _column_exists(inspector, table: str, column: str) -> bool:
    return any(c['name'] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if _column_exists(inspector, 'tenant_face_config', 'verify_mode'):
        return  # 列已存在，幂等跳过

    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.add_column(
            sa.Column(
                'verify_mode',
                sa.String(length=16),
                nullable=False,
                server_default='interface',
            )
        )
        batch_op.create_check_constraint(
            'ck_tenant_face_config_verify_mode',
            "verify_mode IN ('session','interface')",
        )


def downgrade():
    """data-bearing 列：仅在列存在时 drop，避免对未加过列的库报错。"""
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _column_exists(inspector, 'tenant_face_config', 'verify_mode'):
        return

    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.drop_constraint(
            'ck_tenant_face_config_verify_mode', type_='check'
        )
        batch_op.drop_column('verify_mode')
