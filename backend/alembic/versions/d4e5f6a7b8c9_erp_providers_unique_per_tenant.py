"""erp_providers: drop global UNIQUE(provider_name), add UNIQUE(provider_name, tenant_id)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-13 19:00:00.000000

provider_name uniqueness must be scoped to tenant, not global.
"""
from alembic import context, op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def _index_exists(bind, index_name: str) -> bool:
    # offline (--sql) 模式无法查 introspection；视作不存在，直接发 CREATE INDEX DDL。
    if context.is_offline_mode():
        return False
    if bind.dialect.name == 'sqlite':
        result = bind.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"),
            {"n": index_name},
        )
    else:
        result = bind.execute(
            sa.text(
                "SELECT index_name FROM information_schema.statistics "
                "WHERE table_schema=DATABASE() AND index_name=:n LIMIT 1"
            ),
            {"n": index_name},
        )
    return result.first() is not None


def upgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == 'sqlite'

    with op.batch_alter_table('erp_providers', recreate='always' if is_sqlite else 'never') as batch_op:
        try:
            batch_op.drop_constraint('uq_erp_providers_provider_name', type_='unique')
        except Exception:
            pass

    if not _index_exists(bind, 'idx_erp_providers_name_tenant'):
        with op.batch_alter_table('erp_providers') as batch_op:
            batch_op.create_index('idx_erp_providers_name_tenant', ['provider_name', 'tenant_id'], unique=True)


def downgrade():
    with op.batch_alter_table('erp_providers') as batch_op:
        try:
            batch_op.drop_index('idx_erp_providers_name_tenant')
        except Exception:
            pass
        batch_op.create_unique_constraint('uq_erp_providers_provider_name', ['provider_name'])
