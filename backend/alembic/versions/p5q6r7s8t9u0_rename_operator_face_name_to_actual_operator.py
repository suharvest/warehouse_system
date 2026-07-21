"""rename operator_face_name to actual_operator on inventory_records

Revision ID: p5q6r7s8t9u0
Revises: o4p5q6r7s8t9
Create Date: 2026-07-21 12:00:00.000000

产品决策变更：原 operator_face_name（人脸识别姓名快照，展示为 "operator (姓名)"）
提升为独立列 actual_operator（实际操作人），来源有二：
  1. 人脸识别自动填充（原 operator_face_name 承载的值）。
  2. 新增记录表单手工填写。

本迁移把已部署库上的 operator_face_name 列 RENAME 为 actual_operator（保留数据），
兼容 sqlite / MySQL（batch 模式）。幂等：
  * 若 actual_operator 已存在 → no-op；
  * 若仅 operator_face_name 存在 → rename；
  * downgrade 反向。
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'p5q6r7s8t9u0'
down_revision = 'o4p5q6r7s8t9'
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    return any(c['name'] == column for c in inspect(bind).get_columns(table))


def upgrade():
    if context.is_offline_mode():
        # Offline/SQL-generation mode: emit the rename unconditionally.
        with op.batch_alter_table('inventory_records') as batch_op:
            batch_op.alter_column('operator_face_name',
                                  new_column_name='actual_operator',
                                  existing_type=sa.String(length=255))
        return
    if _column_exists('inventory_records', 'actual_operator'):
        return  # 已经是新列名（raw init_database 或早前迁移），幂等跳过
    if _column_exists('inventory_records', 'operator_face_name'):
        with op.batch_alter_table('inventory_records') as batch_op:
            batch_op.alter_column('operator_face_name',
                                  new_column_name='actual_operator',
                                  existing_type=sa.String(length=255))
        return
    # 两列都没有（异常/极旧库）：补建新列，避免下游读写 500。
    op.add_column(
        'inventory_records',
        sa.Column('actual_operator', sa.String(length=255), nullable=True),
    )


def downgrade():
    if context.is_offline_mode():
        with op.batch_alter_table('inventory_records') as batch_op:
            batch_op.alter_column('actual_operator',
                                  new_column_name='operator_face_name',
                                  existing_type=sa.String(length=255))
        return
    if _column_exists('inventory_records', 'operator_face_name'):
        return
    if _column_exists('inventory_records', 'actual_operator'):
        with op.batch_alter_table('inventory_records') as batch_op:
            batch_op.alter_column('actual_operator',
                                  new_column_name='operator_face_name',
                                  existing_type=sa.String(length=255))
