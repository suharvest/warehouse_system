"""unique index on (tenant_id, external_user_id) / (tenant_id, external_warehouse_id)

Revision ID: s8t9u0v1w2x3
Revises: r7s8t9u0v1w2
Create Date: 2026-08-05 02:10:00.000000

导入是「先查后插」：并发导入同一批时两个请求可能各自查空、双双插入，产生重复的
外部账号映射。应用层的检查挡不住并发，所以在数据库层面锁死——同一租户内一个外部
账号只能对应一个本地用户，仓库同理。

NULL 不参与唯一性（SQLite 与 MySQL 均如此），所以手工创建的本地用户/仓库
（external_* 为 NULL）不受影响，可以有任意多条。

建索引前先清理已存在的重复行（保留 id 最小的那条），否则在已有脏数据的库上
建唯一索引会直接失败。
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 's8t9u0v1w2x3'
down_revision = 'r7s8t9u0v1w2'
branch_labels = None
depends_on = None

# (表名, 外部编码列, 索引名)
_TARGETS = (
    ('users', 'external_user_id', 'idx_users_ext_uid_tenant'),
    ('warehouses', 'external_warehouse_id', 'idx_warehouses_ext_wid_tenant'),
)


def _index_exists(inspector, table: str, name: str) -> bool:
    return any(ix['name'] == name for ix in inspector.get_indexes(table))


def upgrade():
    bind = op.get_bind()

    for table, column, index_name in _TARGETS:
        if not context.is_offline_mode():
            inspector = inspect(bind)
            if _index_exists(inspector, table, index_name):
                continue
            # 先去重，否则唯一索引建不起来
            bind.execute(sa.text(f"""
                DELETE FROM {table}
                 WHERE {column} IS NOT NULL
                   AND id NOT IN (
                       SELECT MIN(id) FROM {table}
                        WHERE {column} IS NOT NULL
                        GROUP BY tenant_id, {column}
                   )
            """))
        op.create_index(index_name, table, ['tenant_id', column], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, _column, index_name in _TARGETS:
        if _index_exists(inspector, table, index_name):
            op.drop_index(index_name, table_name=table)
