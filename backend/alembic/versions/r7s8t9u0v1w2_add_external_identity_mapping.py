"""add external identity mapping to users / warehouses

Revision ID: r7s8t9u0v1w2
Revises: q6r7s8t9u0v1
Create Date: 2026-08-04 09:20:00.000000

外部 ERP 模式下库存数据全在对方，但**授权仍由我方判定**：谁能登录、谁能配哪个
智能体、谁能改人脸规则，走的是 users(role, tenant_id) + user_warehouses 这条链。
因此「用户 → 租户/角色」的归属数据不能推给对方，必须落在我方；这两列用来把
我方用户与对方账号对应起来，支撑导入去重与增量同步。

- users.external_user_id
    对应的对方账号 ID，**仅用于导入去重与增量同步**，不参与任何业务链路。
    users 与出入库的 operator、人脸库 face_subjects 都没有关联：operator 是
    自由填写的文本，人脸是单独录入的，users 只决定谁有权修改这些配置。

- warehouses.external_warehouse_id
    仅在**对方系统没有租户概念**时使用：此时仓库是唯一的作用域维度，把对方
    仓库导入为本地行作为权限锚点（user_warehouses 必须绑本地 warehouse_id），
    但不承载任何库存数据。对方有租户概念时这一列为空。

两列都可空，自有模式完全不受影响。幂等：按列是否存在做条件 add，
与 database.py 的 ALTER 兜底路径兼容。
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'r7s8t9u0v1w2'
down_revision = 'q6r7s8t9u0v1'
branch_labels = None
depends_on = None

# (表名, 列名)
_TARGETS = (
    ('users', 'external_user_id'),
    ('warehouses', 'external_warehouse_id'),
)


def _column_exists(inspector, table: str, column: str) -> bool:
    return any(c['name'] == column for c in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    existing = set()
    # offline (--sql) 模式下 bind 是 MockConnection，无法 introspect；直接发 DDL。
    if not context.is_offline_mode():
        inspector = inspect(bind)
        existing = {
            (t, c) for t, c in _TARGETS if _column_exists(inspector, t, c)
        }
        if len(existing) == len(_TARGETS):
            return

    for table, column in _TARGETS:
        if (table, column) in existing:
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column(column, sa.String(128), nullable=True))


def downgrade():
    """data-bearing 列：仅在列存在时 drop，避免对未加过列的库报错。"""
    bind = op.get_bind()
    inspector = inspect(bind)

    for table, column in _TARGETS:
        if not _column_exists(inspector, table, column):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column(column)
