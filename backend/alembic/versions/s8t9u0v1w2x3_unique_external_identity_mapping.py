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

            # 存量重复必须由人来处理，迁移**绝不替运维删数据**。
            # users 被 user_warehouses / inventory_records / sessions / api_keys 引用，
            # warehouses 被 materials / batches / inventory_records 等 8 张表引用——
            # 删一行会打断出入库历史，且 downgrade 恢复不了。
            dupes = bind.execute(sa.text(f"""
                SELECT tenant_id, {column}, COUNT(*) AS cnt
                  FROM {table}
                 WHERE {column} IS NOT NULL AND {column} <> ''
                 GROUP BY tenant_id, {column}
                HAVING COUNT(*) > 1
            """)).fetchall()
            if dupes:
                # 只告警不中止：这个唯一索引是纵深防御（挡并发导入的先查后插），
                # 不是正确性前提。alembic upgrade 跑在应用启动时，为了加固而让整个
                # 仓库系统起不来，代价不成比例。
                detail = "; ".join(
                    f"tenant_id={r[0]} {column}={r[1]!r} x{r[2]}" for r in dupes[:10]
                )
                print(
                    f"\n{'=' * 70}\n"
                    f"[警告] 跳过唯一索引 {index_name}\n"
                    f"{table}.{column} 存在重复的外部映射，无法建立唯一约束。\n"
                    f"本迁移**不会自动删除任何数据** —— {table} 被出入库记录等多张表\n"
                    f"外键引用，删行会打断历史且无法回滚，必须由人工确认保留哪一条。\n\n"
                    f"重复项：{detail}\n\n"
                    f"排查：\n"
                    f"  SELECT id, tenant_id, {column} FROM {table}\n"
                    f"   WHERE {column} IN (SELECT {column} FROM {table}\n"
                    f"                       WHERE {column} IS NOT NULL\n"
                    f"                       GROUP BY tenant_id, {column}\n"
                    f"                      HAVING COUNT(*) > 1);\n\n"
                    f"处理完后重跑 `alembic upgrade head` 即可补上该索引。\n"
                    f"在此之前系统功能不受影响，仅缺少并发导入的数据库级保护。\n"
                    f"{'=' * 70}\n"
                )
                continue
        op.create_index(index_name, table, ['tenant_id', column], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, _column, index_name in _TARGETS:
        if _index_exists(inspector, table, index_name):
            op.drop_index(index_name, table_name=table)
