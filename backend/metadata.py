"""SQLAlchemy Core ``MetaData`` mirroring ``backend.database.init_database``.

Source of truth: ``backend/database.py:68-594``. This module restates that
schema as ``Table(...)`` definitions on a single ``MetaData`` so that:

* Alembic can autogenerate / render migrations for both SQLite and MySQL 8.
* Future code paths can use SQLAlchemy Core against the same physical schema.

The CTO has locked these decisions for phase 1:

* ``materials.sku`` keeps the existing constraints (UNIQUE alone +
  ``UNIQUE(sku, warehouse_id)`` index from ``init_database``).
* Face JSON arrays remain ``JSON`` columns (not normalized).
* ``tenant_id IS NULL`` semantics for the global admin user are preserved.

String length conventions:

* 64  - usernames, slugs, sku, short keys/tags
* 191 - any other indexed/unique TEXT (InnoDB utf8mb4 row size friendly)
* 255 - regular short text (names, addresses, etc.)
* unbounded ``Text`` - long free-form fields (notes, embedding b64,
  configuration blobs that are not indexed)
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)


# Stable constraint names so Alembic autogenerate produces deterministic output.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

# Apply utf8mb4 + ai_ci collation to every table on MySQL. Harmless on SQLite.
MYSQL_TABLE_KW = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def _ts_col(name: str = "created_at", nullable: bool = True) -> Column:
    return Column(name, DateTime, nullable=nullable, server_default=func.current_timestamp())


# ---------------------------------------------------------------------------
# tenants
# ---------------------------------------------------------------------------
tenants = Table(
    "tenants",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("slug", String(64), nullable=False, unique=True),
    Column("name", String(255), nullable=False),
    Column("device_id", String(255), nullable=True, unique=True),
    Column("is_active", Boolean, nullable=False, server_default="1"),
    _ts_col(),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# warehouses
# ---------------------------------------------------------------------------
warehouses = Table(
    "warehouses",
    metadata,
    Column("id", Integer, primary_key=True),
    # slug 在迁移 a1b2c3d4e5f6 之后改为 (slug, tenant_id) 复合 unique；
    # 旧的全局 unique=True 已废弃，仅按 tenant 范围唯一。
    Column("slug", String(64), nullable=False),
    Column("name", String(255), nullable=False),
    Column("address", Text),
    Column("is_default", Boolean, nullable=False, server_default="0"),
    Column("is_disabled", Boolean, nullable=False, server_default="0"),
    _ts_col(),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Index("idx_warehouses_tenant", "tenant_id"),
    Index("idx_warehouses_slug_tenant", "slug", "tenant_id", unique=True),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# users  (declared early so user_warehouses + others can FK it)
# ---------------------------------------------------------------------------
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    # username 在迁移 c3d4e5f6a7b8 之后改为 (username, tenant_id) 复合 unique；
    # 同一用户名允许在不同租户里复用。
    Column("username", String(64), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(32), nullable=False, server_default="view"),
    Column("display_name", String(255)),
    Column("is_disabled", Boolean, nullable=False, server_default="0"),
    _ts_col(),
    Column("created_by", Integer, ForeignKey("users.id")),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Column("last_login_at", DateTime, nullable=True),
    Index("idx_users_tenant", "tenant_id"),
    Index("idx_users_username_tenant", "username", "tenant_id", unique=True),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# user_warehouses
# ---------------------------------------------------------------------------
user_warehouses = Table(
    "user_warehouses",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("warehouse_id", Integer, ForeignKey("warehouses.id"), nullable=False),
    UniqueConstraint("user_id", "warehouse_id", name="uq_user_warehouses_user_id_warehouse_id"),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------
materials = Table(
    "materials",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(255), nullable=False),
    # sku 在迁移 a1b2c3d4e5f6 之后改为 (sku, warehouse_id) 复合 unique；
    # 同一 SKU 允许在不同仓库里复用。索引声明见下方 idx_materials_sku_wh。
    Column("sku", String(64), nullable=False),
    Column("category", String(64), nullable=False),
    Column("quantity", Integer, server_default="0"),
    Column("unit", String(16), server_default="个"),
    Column("safe_stock", Integer, nullable=True),
    # 用户实际会塞备注（"二号架顶层 / 备件区 / 注意防潮"），255 容易吃满。
    Column("location", String(512)),
    Column("is_disabled", Boolean, nullable=False, server_default="0"),
    Column("warehouse_id", Integer, ForeignKey("warehouses.id")),
    _ts_col(),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Index("idx_materials_warehouse", "warehouse_id"),
    Index("idx_materials_tenant", "tenant_id"),
    Index("idx_materials_sku_wh", "sku", "warehouse_id", unique=True),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# contacts (declared before inventory_records / batches to satisfy FK refs)
# ---------------------------------------------------------------------------
contacts = Table(
    "contacts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(255), nullable=False),
    Column("address", Text),
    Column("phone", String(64)),
    Column("email", String(255)),
    Column("is_supplier", Boolean, nullable=False, server_default="0"),
    Column("is_customer", Boolean, nullable=False, server_default="0"),
    Column("notes", Text),
    Column("is_disabled", Boolean, nullable=False, server_default="0"),
    _ts_col(),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    # contacts.warehouse_id was added historically and then nulled out; keep
    # the column so existing rows don't lose data, but no index.
    Column("warehouse_id", Integer, ForeignKey("warehouses.id")),
    Index("idx_contacts_tenant", "tenant_id"),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# batches
# ---------------------------------------------------------------------------
batches = Table(
    "batches",
    metadata,
    Column("id", Integer, primary_key=True),
    # batch_no 在迁移 a1b2c3d4e5f6 之后改为 (batch_no, warehouse_id) 复合 unique；
    # 同一批次号允许在不同仓库里复用（实际由 generate_batch_no 按仓库递增）。
    # 索引声明见下方 idx_batches_no_wh。
    Column("batch_no", String(64), nullable=False),
    Column("material_id", Integer, ForeignKey("materials.id"), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("initial_quantity", Integer, nullable=False),
    Column("contact_id", Integer, ForeignKey("contacts.id")),
    Column("is_exhausted", Boolean, nullable=False, server_default="0"),
    Column("warehouse_id", Integer, ForeignKey("warehouses.id")),
    _ts_col(),
    # 与 materials.location 对齐：实际会被用户当成备注塞。
    Column("location", String(512)),
    Column("variant", String(191)),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Index("idx_batches_warehouse", "warehouse_id"),
    Index("idx_batches_tenant", "tenant_id"),
    Index("idx_batches_no_wh", "batch_no", "warehouse_id", unique=True),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# inventory_records
# ---------------------------------------------------------------------------
inventory_records = Table(
    "inventory_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("material_id", Integer, ForeignKey("materials.id"), nullable=False),
    Column("type", String(8), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("operator", String(64), server_default="系统"),
    # 旧的 reason 自由文本列已被 reason_category + reason_note 取代，迁移 1826e23835b6 之后无人写入。
    Column("reason_category", String(32)),
    Column("reason_note", Text),
    _ts_col(),
    Column("contact_id", Integer, ForeignKey("contacts.id")),
    Column("batch_id", Integer, ForeignKey("batches.id")),
    Column("operator_user_id", Integer, ForeignKey("users.id")),
    # 人脸识别通过时识别到的人员姓名快照（face_subjects.name），写入时冻结；
    # 展示层组合为 "operator (姓名)"。非人脸链路写入为 NULL。
    Column("operator_face_name", String(255)),
    Column("warehouse_id", Integer, ForeignKey("warehouses.id")),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Index("idx_records_warehouse", "warehouse_id"),
    Index("idx_records_tenant", "tenant_id"),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
sessions = Table(
    "sessions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("token", String(191), nullable=False, unique=True),
    Column("expires_at", DateTime, nullable=False),
    Column("revoked_at", DateTime),
    _ts_col(),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# api_keys
# ---------------------------------------------------------------------------
api_keys = Table(
    "api_keys",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("key_hash", String(191), nullable=False, unique=True),
    Column("name", String(255), nullable=False),
    Column("role", String(32), nullable=False, server_default="operate"),
    Column("user_id", Integer, ForeignKey("users.id")),
    Column("is_disabled", Boolean, nullable=False, server_default="0"),
    Column("is_system", Boolean, nullable=False, server_default="0"),
    _ts_col(),
    Column("last_used_at", DateTime),
    Column("warehouse_id", Integer, ForeignKey("warehouses.id")),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Index("idx_api_keys_tenant", "tenant_id"),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# system_settings
# ---------------------------------------------------------------------------
system_settings = Table(
    "system_settings",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", DateTime, server_default=func.current_timestamp()),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# erp_providers
# ---------------------------------------------------------------------------
erp_providers = Table(
    "erp_providers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(255), nullable=False),
    # provider_name 在迁移 d4e5f6a7b8c9 之后改为 (provider_name, tenant_id) 复合 unique。
    Column("provider_name", String(64), nullable=False),
    Column("class_name", String(255), nullable=False),
    Column("filename", String(255), nullable=False),
    Column("config", JSON),
    Column("test_results", JSON),
    Column("test_passed_at", DateTime),
    Column("is_active", Boolean, nullable=False, server_default="0"),
    _ts_col(),
    Column("updated_at", DateTime, server_default=func.current_timestamp()),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Index("idx_erp_providers_name_tenant", "provider_name", "tenant_id", unique=True),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# mcp_connections
# ---------------------------------------------------------------------------
mcp_connections = Table(
    "mcp_connections",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    # 真实场景里 mcp_endpoint 常带 JWT/access-token query 参数（500-2000 字符），用 Text 兜底。
    # SQLite 视 String 与 Text 等价（都是 TEXT），无影响；MySQL 上 String(255) 会触发
    # "Data too long" 报错（实测 Seeed watcher endpoint 含 ES256 JWT 时约 400+ 字符）。
    Column("mcp_endpoint", Text, nullable=False),
    # api_key 与 mcp_endpoint 同源：第三方 MCP server 颁发的 API key / bearer token 常为长 JWT，
    # MySQL VARCHAR(255) 会触发 1406 Data too long。改 Text 兜底。未参与索引/等值查询。
    Column("api_key", Text, nullable=False),
    Column("role", String(32), nullable=False, server_default="operate"),
    Column("auto_start", Boolean, nullable=False, server_default="1"),
    Column("status", String(32), server_default="stopped"),
    Column("error_message", Text),
    Column("restart_count", Integer, server_default="0"),
    Column("debug_mode", Integer, server_default="0"),
    # init_database stores these as TEXT (ISO strings).
    Column("created_at", String(32)),
    Column("updated_at", String(32)),
    Column("warehouse_id", Integer, ForeignKey("warehouses.id")),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Column("device_id", String(64), nullable=True, unique=True),
    Index("idx_mcp_connections_tenant", "tenant_id"),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# mcp_agent_devices — physical devices attached to one cloud agent
# ---------------------------------------------------------------------------
# 一个 mcp_connections 行 = 一个云端智能体端点；其下可挂多个物理设备。
# 这张一对多子表存每个设备的 LAN IP / NPU 模型标签等，供后续"云端下发人脸库
# 到设备"按 model_tag 过滤后下发到 ip:port。connection_id ondelete CASCADE：
# 智能体删了，挂在它下面的设备记录跟着删。
mcp_agent_devices = Table(
    "mcp_agent_devices",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "connection_id",
        String(64),
        ForeignKey("mcp_connections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # 物理设备标识（MAC / 序列号），可空；同一智能体下不可重复（见 UniqueConstraint）。
    Column("device_id", String(128), nullable=True),
    Column("name", String(255), nullable=True),
    # LAN IP — DB 层可空，API 层校验非空（与 port 一起做 1-65535 校验）。
    Column("ip", String(64), nullable=True),
    Column("port", Integer, nullable=False, server_default="80"),
    # 设备 NPU embedding 模型标签；下发人脸库时按它过滤可用的 enrollment。
    Column("model_tag", String(64), nullable=True),
    # opt-in 人脸：0=不下发人脸库到该设备，1=下发。存 0/1（sqlite/MySQL 通用）。
    Column("face_enabled", Integer, nullable=False, server_default="0"),
    # 后端直拉设备身份（B 方案）的鉴权 token，每设备独立、可轮换。随人脸库
    # 一并下发到设备 NVS face.pull_token；后端拉取时置于 X-Face-Token。与远端识别
    # 的 identify_token（仅 lan 模式有值）刻意分离，避免复用扩大泄露面。
    Column("pull_token", String(64), nullable=True),
    Column("last_seen", String(32), nullable=True),
    Column("created_at", String(32)),
    Column("updated_at", String(32)),
    Index("idx_mcp_agent_devices_connection", "connection_id"),
    UniqueConstraint(
        "connection_id",
        "device_id",
        name="uq_mcp_agent_devices_connection_id_device_id",
    ),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# Face recognition tables
# ---------------------------------------------------------------------------
tenant_face_config = Table(
    "tenant_face_config",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "tenant_id",
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("enabled", Boolean, nullable=False, server_default="0"),
    Column("mode", String(16)),
    # 同 mcp_endpoint：人脸服务 URL 常带 JWT/token query 参数，auth_token 直接是 JWT。
    # 用 Text 避免 VARCHAR(255) 在 MySQL 上撑爆。两列都不被索引/等值查询。
    Column("endpoint", Text),
    Column("auth_token", Text),
    Column("embedding_model_tag", String(64)),
    Column("min_confidence", Float, nullable=False, server_default="0.45"),
    # Passive greeting (visual wake) on/off — independent of the out-of-stock
    # auth switch (`enabled`). xiaozhi reads this on device connect / voice sync
    # and aligns the device's local passive-recognition state via self.face.enable.
    Column("greeting_enabled", Boolean, nullable=False, server_default="0"),
    # DEPRECATED: verify_mode（旧「鉴权强度」）已被 mode + verify_frequency 取代。
    # 验证链路现在只看 mode（local=设备拉身份 / lan=端点重比对），代码不再读本列；
    # 列保留以支持旧版本回滚（PUT /api/face/config 仍反向同步写入保持一致）。
    Column("verify_mode", String(16), nullable=False, server_default="interface"),
    # 人脸验证频率（与 mode 正交，只控制会话缓存）：
    #   always  — 每次操作都现场验证（默认）
    #   session — 同一会话首次验证通过后，之后免验（session_cached）
    Column("verify_frequency", String(16), nullable=False, server_default="always"),
    Column("created_at", String(32), server_default=func.current_timestamp()),
    Column("updated_at", String(32), server_default=func.current_timestamp()),
    CheckConstraint("mode IN ('local','lan')", name="ck_tenant_face_config_mode"),
    CheckConstraint(
        "verify_mode IN ('session','interface')",
        name="ck_tenant_face_config_verify_mode",
    ),
    CheckConstraint(
        "verify_frequency IN ('always','session')",
        name="ck_tenant_face_config_verify_frequency",
    ),
    **MYSQL_TABLE_KW,
)


face_subjects = Table(
    "face_subjects",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "tenant_id",
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String(255), nullable=False),
    Column("employee_id", String(64)),
    Column("note", Text),
    Column("is_active", Boolean, nullable=False, server_default="1"),
    Column("created_by", Integer),
    Column("created_at", String(32), server_default=func.current_timestamp()),
    Column("updated_at", String(32), server_default=func.current_timestamp()),
    Index("idx_face_subjects_tenant", "tenant_id", "is_active"),
    **MYSQL_TABLE_KW,
)


tenant_face_operation_rules = Table(
    "tenant_face_operation_rules",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "tenant_id",
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "warehouse_id",
        Integer,
        ForeignKey("warehouses.id", ondelete="CASCADE"),
    ),
    Column("operation", String(64), nullable=False),
    Column("require_face", Boolean, nullable=False, server_default="0"),
    Column("allowed_subject_ids", JSON),
    Column("min_confidence_override", Float),
    Index("idx_face_rules_lookup", "tenant_id", "warehouse_id", "operation"),
    **MYSQL_TABLE_KW,
)


face_enrollments = Table(
    "face_enrollments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "subject_id",
        Integer,
        ForeignKey("face_subjects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "tenant_id",
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("model_tag", String(64), nullable=False),
    Column("embedding", LargeBinary, nullable=False),
    Column("source_image_b64", Text),
    Column("applies_to_warehouse_ids", JSON),
    Column("is_active", Boolean, nullable=False, server_default="1"),
    Column("enrolled_at", String(32), server_default=func.current_timestamp()),
    Column("enrolled_by", Integer),
    Index("idx_face_enroll", "tenant_id", "model_tag", "is_active"),
    Index("idx_face_enroll_subject", "subject_id"),
    **MYSQL_TABLE_KW,
)


face_auth_logs = Table(
    "face_auth_logs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("request_id", String(64)),
    Column("user_id", Integer, nullable=False),
    Column("matched_subject_id", Integer),
    Column("tenant_id", Integer, nullable=False),
    Column("warehouse_id", Integer),
    Column("operation", String(64), nullable=False),
    Column("confidence", Float),
    Column("decision", String(16), nullable=False),
    Column("failure_reason", Text),
    Column("created_at", String(32), server_default=func.current_timestamp()),
    CheckConstraint(
        "decision IN ('pass','deny','skipped')",
        name="ck_face_auth_logs_decision",
    ),
    Index("idx_face_logs_query", "tenant_id", "created_at"),
    **MYSQL_TABLE_KW,
)


# ---------------------------------------------------------------------------
# batch_consumptions (declared last; FKs to inventory_records and batches)
# ---------------------------------------------------------------------------
batch_consumptions = Table(
    "batch_consumptions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "record_id",
        Integer,
        ForeignKey("inventory_records.id"),
        nullable=False,
    ),
    Column("batch_id", Integer, ForeignKey("batches.id"), nullable=False),
    Column("quantity", Integer, nullable=False),
    _ts_col(),
    Column("tenant_id", Integer, ForeignKey("tenants.id"), server_default="1"),
    Column("warehouse_id", Integer, ForeignKey("warehouses.id")),
    Index("idx_bc_tenant", "tenant_id"),
    Index("idx_bc_warehouse", "warehouse_id"),
    **MYSQL_TABLE_KW,
)


target_metadata = metadata


__all__ = [
    "metadata",
    "target_metadata",
    "tenants",
    "warehouses",
    "user_warehouses",
    "users",
    "materials",
    "contacts",
    "batches",
    "inventory_records",
    "sessions",
    "api_keys",
    "system_settings",
    "erp_providers",
    "mcp_connections",
    "mcp_agent_devices",
    "tenant_face_config",
    "face_subjects",
    "tenant_face_operation_rules",
    "face_enrollments",
    "face_auth_logs",
    "batch_consumptions",
]
