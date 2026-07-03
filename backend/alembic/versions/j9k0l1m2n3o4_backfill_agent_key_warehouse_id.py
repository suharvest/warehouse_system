"""backfill NULL warehouse_id on agent api_keys and mcp_connections

Revision ID: j9k0l1m2n3o4
Revises: i8j9k0l1m2n3
Create Date: 2026-06-29 12:00:00.000000

仓库作用域上线时，存量的 agent 资源没有被回填 warehouse_id：

  - mcp_connections.warehouse_id = NULL  → 智能体配置列表按 warehouse_id 过滤时被吞
  - api_keys.warehouse_id = NULL（is_system 的 "Agent: xxx" key，role=operate）
    → 该 key 调后端时 build_authorized_scope_predicates 走 "无授权仓库 → false()"，
      任何物料编号都查不到，智能体表现为“系统里查不到物料编码 X，这个物料不存在”。

本迁移把这两类 NULL 回填到各自租户的默认仓库（warehouses.is_default=1）。

幂等：只更新 warehouse_id IS NULL 的行；无默认仓库的租户用 EXISTS 跳过（不会写成 NULL）。
不动 role='admin' 的 key（其 NULL 语义是“全仓可见”，绑定单仓反而会缩小权限）。
data-only 迁移，downgrade 无法可靠还原（哪些原本是 NULL 不可知），故为 no-op。

子查询 FROM 的是 warehouses（与被更新表不同），MySQL/sqlite 均可。
"""
from alembic import op

revision = 'j9k0l1m2n3o4'
down_revision = 'i8j9k0l1m2n3'
branch_labels = None
depends_on = None


def upgrade():
    # 1) agent api_keys（系统创建、非 admin 角色）
    op.execute(
        """
        UPDATE api_keys
        SET warehouse_id = (
            SELECT w.id FROM warehouses w
            WHERE w.tenant_id = api_keys.tenant_id AND w.is_default = 1
            ORDER BY w.id LIMIT 1
        )
        WHERE warehouse_id IS NULL
          AND is_system = 1
          AND role <> 'admin'
          AND EXISTS (
            SELECT 1 FROM warehouses w2
            WHERE w2.tenant_id = api_keys.tenant_id AND w2.is_default = 1
          )
        """
    )

    # 2) mcp_connections
    op.execute(
        """
        UPDATE mcp_connections
        SET warehouse_id = (
            SELECT w.id FROM warehouses w
            WHERE w.tenant_id = mcp_connections.tenant_id AND w.is_default = 1
            ORDER BY w.id LIMIT 1
        )
        WHERE warehouse_id IS NULL
          AND EXISTS (
            SELECT 1 FROM warehouses w2
            WHERE w2.tenant_id = mcp_connections.tenant_id AND w2.is_default = 1
          )
        """
    )


def downgrade():
    # data-only 回填，无法可靠还原（无法区分哪些原本就是 NULL），no-op。
    pass
