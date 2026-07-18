"""add verify_frequency to tenant_face_config

Revision ID: m2n3o4p5q6r7
Revises: l1m2n3o4p5q6
Create Date: 2026-07-18 10:00:00.000000

mode 与「验证频率」解耦：

- 旧语义把 verify_mode 同时当「鉴权链路」和「验证频率」用，前端还把它展示成
  「人脸验证频率」，概念混淆。
- 新语义：验证链路只看 mode（local=后端直拉设备身份 / lan=端点重比对）；
  verify_frequency 只控制会话缓存：
    always  — 每次操作都现场验证（默认）
    session — 同会话首验通过后免验（session_cached）
- verify_mode 列保留不删（旧版本回滚兼容），代码不再读取。

数据回填：verify_mode='session' → verify_frequency='session'；'interface' →
'always'（列默认值，无需显式 UPDATE）。

幂等性：raw init_database()（backend/database.py）同步加了该列 + 回填兜底，
走 raw 路径建的库此处发现列已存在则跳过；inspector guard 兼容两种 db 状态。
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'm2n3o4p5q6r7'
down_revision = 'l1m2n3o4p5q6'
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    return any(c['name'] == column for c in inspect(bind).get_columns(table))


def upgrade():
    if _column_exists('tenant_face_config', 'verify_frequency'):
        return  # raw init_database 已建列并回填，幂等跳过

    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.add_column(
            sa.Column(
                'verify_frequency',
                sa.String(length=16),
                nullable=False,
                server_default='always',
            )
        )
        batch_op.create_check_constraint(
            'ck_tenant_face_config_verify_frequency',
            "verify_frequency IN ('always','session')",
        )
    # 回填：旧 verify_mode='session' 的租户维持「仅首次验证」体验。
    op.execute(
        "UPDATE tenant_face_config SET verify_frequency = 'session' "
        "WHERE verify_mode = 'session'"
    )


def downgrade():
    if not _column_exists('tenant_face_config', 'verify_frequency'):
        return
    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.drop_constraint(
            'ck_tenant_face_config_verify_frequency', type_='check'
        )
        batch_op.drop_column('verify_frequency')
