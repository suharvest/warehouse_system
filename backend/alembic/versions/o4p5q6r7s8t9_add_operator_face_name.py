"""add operator_face_name to inventory_records

Revision ID: o4p5q6r7s8t9
Revises: n3o4p5q6r7s8
Create Date: 2026-07-21 10:00:00.000000

人脸识别通过写库时，把识别到的人员姓名（face_subjects.name）快照进
inventory_records.operator_face_name，展示/导出层组合为 "operator (姓名)"。
非人脸链路（手工/导入/移库等）写 NULL。

幂等性：raw init_database()（backend/database.py）同步加了该列，走 raw 路径
建的库此处发现列已存在则跳过；inspector guard 兼容两种 db 状态。
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'o4p5q6r7s8t9'
down_revision = 'n3o4p5q6r7s8'
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    return any(c['name'] == column for c in inspect(bind).get_columns(table))


def upgrade():
    if _column_exists('inventory_records', 'operator_face_name'):
        return  # raw init_database 已建列，幂等跳过
    op.add_column(
        'inventory_records',
        sa.Column('operator_face_name', sa.String(length=255), nullable=True),
    )


def downgrade():
    if not _column_exists('inventory_records', 'operator_face_name'):
        return
    with op.batch_alter_table('inventory_records') as batch_op:
        batch_op.drop_column('operator_face_name')
