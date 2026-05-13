"""make username unique per tenant instead of globally

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-13 00:01:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == 'sqlite'
    # Drop the global UNIQUE(username) constraint (recreate table for SQLite)
    with op.batch_alter_table('users', recreate='always' if is_sqlite else 'never') as batch_op:
        batch_op.drop_constraint('uq_users_username', type_='unique')
    # UNIQUE(username, tenant_id): allows same name across tenants,
    # NULL-safe for global admin (tenant_id IS NULL).
    with op.batch_alter_table('users') as batch_op:
        batch_op.create_index('idx_users_username_tenant', ['username', 'tenant_id'], unique=True)


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        try:
            batch_op.drop_index('idx_users_username_tenant')
        except Exception:
            pass
        batch_op.create_unique_constraint('uq_users_username', ['username'])
