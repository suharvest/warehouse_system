"""widen mcp_connections.mcp_endpoint to TEXT

Revision ID: a3b4c5d6e7f8
Revises: 6fec76bb57d9
Create Date: 2026-05-11 16:10:00.000000

mcp_endpoint 在 SQLite 上是 TEXT 无长度限制，但 SA Core 元数据声明的是 String(255)，
切到 MySQL 时该列变成 VARCHAR(255)。Seeed watcher 等真实场景下 endpoint 会附带
ES256 JWT token 作为 query 参数，整串常超过 400 字符，触发：

    DataError: (1406, "Data too long for column 'mcp_endpoint' at row 1")

把这列扩到 TEXT，根治这类长度截断问题。SQLite 端 alter_column 是 no-op（SQLite 不
强制 VARCHAR 长度），但通过 batch_alter_table 保持兼容。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = '6fec76bb57d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('mcp_connections') as batch_op:
        batch_op.alter_column(
            'mcp_endpoint',
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    # 注意：若库中已存在 >255 字符的行，本回滚会失败/截断。
    with op.batch_alter_table('mcp_connections') as batch_op:
        batch_op.alter_column(
            'mcp_endpoint',
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=False,
        )
