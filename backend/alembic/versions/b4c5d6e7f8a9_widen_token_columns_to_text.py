"""widen JWT/token-bearing VARCHAR(255) columns to TEXT

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-05-11 16:15:00.000000

Follow-up to a3b4c5d6e7f8 (which widened mcp_connections.mcp_endpoint).
Audit of metadata.py turned up three more columns with the same shape —
declared String(255) but in production they store URLs/tokens that
routinely exceed 255 chars (JWT, OAuth bearer, etc.). All three are
read whole, never used in indexes or equality WHERE lookups, so the
MySQL "TEXT can't be prefix-indexed" limitation does not apply.

Columns affected:
- mcp_connections.api_key      (third-party MCP server tokens — JWT)
- tenant_face_config.endpoint  (face service URL, may carry token in query)
- tenant_face_config.auth_token (bearer/JWT for face service)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('mcp_connections') as batch_op:
        batch_op.alter_column(
            'api_key',
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=False,
        )

    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.alter_column(
            'endpoint',
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=True,
        )
        batch_op.alter_column(
            'auth_token',
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # 若库中存在 >255 字符的行，downgrade 会截断/失败。
    with op.batch_alter_table('tenant_face_config') as batch_op:
        batch_op.alter_column(
            'auth_token',
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=True,
        )
        batch_op.alter_column(
            'endpoint',
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=True,
        )

    with op.batch_alter_table('mcp_connections') as batch_op:
        batch_op.alter_column(
            'api_key',
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=False,
        )
