"""tenant_face_config.min_confidence 默认值 0.65 → 0.45

仅调整列的 server_default（影响之后新建的行）；已保存的租户配置值不动。

Revision ID: n3o4p5q6r7s8
Revises: m2n3o4p5q6r7
Create Date: 2026-07-20
"""
import sqlalchemy as sa
from alembic import op

revision = 'n3o4p5q6r7s8'
down_revision = 'm2n3o4p5q6r7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.alter_column(
            'min_confidence',
            existing_type=sa.Float(),
            existing_nullable=False,
            server_default='0.45',
        )


def downgrade():
    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.alter_column(
            'min_confidence',
            existing_type=sa.Float(),
            existing_nullable=False,
            server_default='0.65',
        )
