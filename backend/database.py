import sqlite3
import os
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional
import random

# R3: wire-format string enums. Support both ``backend.database`` (package)
# and bare ``database`` (sys.path-tweaked) import styles used across tests.
try:
    from models import RecordType, RoleName  # type: ignore
except ImportError:  # pragma: no cover
    from backend.models import RecordType, RoleName  # type: ignore

# 尝试导入bcrypt（生产环境使用）
try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False

# ============================================
# 出入库原因分类常量
# ============================================
REASON_CATEGORIES = {
    'in': ['purchase', 'return', 'refund', 'produce', 'transfer_in', 'other_in'],
    'out': ['sell', 'lend', 'consume', 'loss', 'transfer_out', 'other_out'],
}

REASON_CATEGORY_LABELS = {
    'purchase': '采购入库', 'return': '借还', 'refund': '退货入库',
    'produce': '生产入库', 'transfer_in': '调拨入库', 'other_in': '其他',
    'sell': '出售', 'lend': '借出', 'consume': '领用/消耗',
    'loss': '损耗/损失', 'transfer_out': '调拨出库', 'other_out': '其他',
}

# 历史数据迁移映射：旧 reason 文本 → 新 reason_category
REASON_MIGRATION_MAP = {
    '采购入库': 'purchase', '退货入库': 'refund',
    '生产完工入库': 'produce', '生产入库': 'produce',
    '调拨入库': 'transfer_in', '借还': 'return',
    '销售出库': 'sell', '借出': 'lend',
    '领用': 'consume', '消耗': 'consume',
    '研发领用': 'consume', '生产领料': 'consume',
    '损耗': 'loss', '调拨出库': 'transfer_out',
    '返修出库': 'other_out',
}

# ============================================
# 环境变量配置
# ============================================
# 数据库路径
# 默认锚定到项目根（backend/ 的上一级），避免因启动时 cwd 不同而出现重复的 warehouse.db
_DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'warehouse.db')
DATABASE_PATH = os.environ.get('DATABASE_PATH', _DEFAULT_DB_PATH)
# 是否启用bcrypt密码哈希（默认true，需要bcrypt可用）
BCRYPT_ENABLED = os.environ.get('BCRYPT_ENABLED', 'true').lower() == 'true' and BCRYPT_AVAILABLE
# 是否启用SQLite生产优化（WAL模式、外键约束等）
SQLITE_PRODUCTION_MODE = os.environ.get('SQLITE_PRODUCTION_MODE', 'false').lower() == 'true'


def _is_sqlite() -> bool:
    """是否使用 SQLite 引擎（基于 DATABASE_URL）。"""
    url = os.environ.get('DATABASE_URL', '')
    if url:
        return url.startswith('sqlite')
    # 默认走 sqlite (DATABASE_PATH)
    return True


def get_db_connection():
    """获取数据库连接。

    SQLite 路径：返回 sqlite3.Connection（保持向后兼容，row_factory=Row）。
    其他方言（MySQL）：返回一个 sqlite3-API-shaped shim，包装 SQLAlchemy Connection，
    自动把 ``?`` 占位符翻译成 ``%s`` / ``:p0`` 等当前驱动支持的形式。

    这是从 raw sqlite3 -> SQLAlchemy Core 渐进迁移的"软桥"。新代码请直接走
    ``backend.db.get_engine()``。
    """
    if _is_sqlite():
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row

        # 生产模式下启用优化配置
        if SQLITE_PRODUCTION_MODE:
            cursor = conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')       # 更好的并发性能
            cursor.execute('PRAGMA foreign_keys=ON')        # 启用外键约束
            cursor.execute('PRAGMA synchronous=NORMAL')     # 平衡安全与性能
            cursor.execute('PRAGMA cache_size=-64000')      # 64MB缓存

        return conn

    # MySQL / 其他方言 → 走 shim
    from db import get_engine
    return _SAConnectionShim(get_engine())


class _RowShim(dict):
    """模拟 sqlite3.Row：既能 ``row['col']`` 也能 ``row[0]`` 访问。"""

    def __init__(self, mapping, columns):
        super().__init__(mapping)
        self._columns = columns

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.get(self._columns[key])
        return super().__getitem__(key)

    @property
    def keys_list(self):
        return list(self._columns)


class _CursorShim:
    """模拟 sqlite3.Cursor，包一个 SQLAlchemy Connection。

    支持：
    - ``execute(sql, params=())``：把 ``?`` 翻译成 ``%s`` 然后通过 ``text()`` 执行；
      会自动开 / 沿用一个事务（commit 在父连接的 ``commit()`` 上）。
    - ``executemany(sql, seq_of_params)``：循环 execute。
    - ``fetchone() / fetchall()``：返回 ``_RowShim``。
    - ``lastrowid``：从 ``CursorResult.lastrowid`` 取（INSERT 后）。
    - ``rowcount``：从 ``CursorResult.rowcount`` 取。
    """

    def __init__(self, parent: '_SAConnectionShim'):
        self._parent = parent
        self._result = None
        self._columns: list[str] = []
        self._rows_consumed = False
        self.lastrowid = None
        self.rowcount = -1

    def _translate_sql(self, sql: str) -> str:
        # SQLAlchemy text() 用 ``:name`` 形式。把 ``?`` 替换成 ``:p0, :p1, ...``。
        if '?' not in sql:
            return sql
        out = []
        idx = 0
        in_str = False
        quote_char = ''
        for ch in sql:
            if in_str:
                out.append(ch)
                if ch == quote_char:
                    in_str = False
                continue
            if ch in ("'", '"'):
                in_str = True
                quote_char = ch
                out.append(ch)
                continue
            if ch == '?':
                out.append(f':p{idx}')
                idx += 1
            else:
                out.append(ch)
        return ''.join(out)

    def execute(self, sql: str, params=()):
        from sqlalchemy import text as _sa_text
        translated = self._translate_sql(sql)
        if isinstance(params, dict):
            bind = params
        else:
            bind = {f'p{i}': v for i, v in enumerate(params or ())}
        conn = self._parent._ensure_conn()
        result = conn.execute(_sa_text(translated), bind)
        self._result = result
        try:
            self._columns = list(result.keys()) if result.returns_rows else []
        except Exception:
            self._columns = []
        try:
            self.lastrowid = result.lastrowid
        except Exception:
            self.lastrowid = None
        try:
            self.rowcount = result.rowcount
        except Exception:
            self.rowcount = -1
        self._rows_consumed = False
        return self

    def executemany(self, sql, seq_of_params):
        last = None
        for p in seq_of_params:
            last = self.execute(sql, p)
        return last

    def fetchone(self):
        if self._result is None or self._rows_consumed:
            return None
        row = self._result.fetchone()
        if row is None:
            return None
        mapping = dict(row._mapping) if hasattr(row, '_mapping') else dict(zip(self._columns, row))
        return _RowShim(mapping, self._columns)

    def fetchall(self):
        if self._result is None or self._rows_consumed:
            return []
        rows = self._result.fetchall()
        self._rows_consumed = True
        out = []
        for r in rows:
            mapping = dict(r._mapping) if hasattr(r, '_mapping') else dict(zip(self._columns, r))
            out.append(_RowShim(mapping, self._columns))
        return out

    def close(self):
        self._result = None


class _SAConnectionShim:
    """模拟 sqlite3.Connection，包一个 SQLAlchemy Engine。

    每次 ``cursor()`` / ``execute()`` 操作都共用一个 lazily-opened SA Connection
    与 ``Transaction``。``commit()`` 提交、``close()`` 关闭。
    """

    def __init__(self, engine):
        self._engine = engine
        self._sa_conn = None
        self._sa_tx = None
        self.row_factory = None  # 兼容写入但忽略

    def _ensure_conn(self):
        if self._sa_conn is None:
            self._sa_conn = self._engine.connect()
        if self._sa_tx is None:
            self._sa_tx = self._sa_conn.begin()
        return self._sa_conn

    def cursor(self):
        return _CursorShim(self)

    def execute(self, sql, params=()):
        # sqlite3.Connection.execute 等价于 cursor.execute，返回 cursor
        cur = _CursorShim(self)
        return cur.execute(sql, params)

    def commit(self):
        if self._sa_tx is not None:
            try:
                self._sa_tx.commit()
            finally:
                self._sa_tx = None
        # 立即开一个新事务以承载下一批 statement（贴合 sqlite3 的隐式事务习惯）
        if self._sa_conn is not None:
            self._sa_tx = self._sa_conn.begin()

    def rollback(self):
        if self._sa_tx is not None:
            try:
                self._sa_tx.rollback()
            finally:
                self._sa_tx = None
        if self._sa_conn is not None:
            self._sa_tx = self._sa_conn.begin()

    def close(self):
        try:
            if self._sa_tx is not None:
                # 没显式 commit 的事务回滚（与 sqlite3 行为一致）
                try:
                    self._sa_tx.rollback()
                except Exception:
                    pass
                self._sa_tx = None
        finally:
            if self._sa_conn is not None:
                try:
                    self._sa_conn.close()
                finally:
                    self._sa_conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

def init_database():
    """初始化数据库表结构（仅 SQLite）。

    非 sqlite 方言（MySQL）下，schema 由 alembic 管理，本函数是 no-op。
    保留 sqlite 路径主要服务于历史测试夹具与本地开发。
    """
    if not _is_sqlite():
        return
    conn = get_db_connection()
    cursor = conn.cursor()

    # 创建租户表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tenants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 创建仓库表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warehouses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            address TEXT,
            is_default INTEGER DEFAULT 0,
            is_disabled INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 创建用户-仓库授权表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_warehouses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
            UNIQUE(user_id, warehouse_id)
        )
    ''')

    # 确保默认仓库存在
    cursor.execute('''
        INSERT OR IGNORE INTO warehouses (slug, name, is_default) VALUES ('default', '默认仓库', 1)
    ''')

    # 确保默认租户存在
    cursor.execute('''
        INSERT OR IGNORE INTO tenants (slug, name) VALUES ('default', '默认租户')
    ''')

    # 创建物料表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sku TEXT NOT NULL,
            category TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            unit TEXT DEFAULT '个',
            safe_stock INTEGER DEFAULT NULL,
            location TEXT,
            is_disabled INTEGER DEFAULT 0,
            warehouse_id INTEGER REFERENCES warehouses(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 检查并添加 is_disabled 字段（用于已存在的数据库）
    try:
        cursor.execute('SELECT is_disabled FROM materials LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE materials ADD COLUMN is_disabled INTEGER DEFAULT 0')

    # 检查并添加 warehouse_id 字段到 materials
    try:
        cursor.execute('SELECT warehouse_id FROM materials LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE materials ADD COLUMN warehouse_id INTEGER REFERENCES warehouses(id)')

    # 创建出入库记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            operator TEXT DEFAULT '系统',
            reason_category TEXT,
            reason_note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials (id)
        )
    ''')

    # 创建用户表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'view',
            display_name TEXT,
            is_disabled INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER REFERENCES users(id),
            tenant_id INTEGER DEFAULT 1,
            last_login_at TIMESTAMP
        )
    ''')

    # 创建会话表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            revoked_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # 检查并添加 revoked_at 字段（用于已存在的数据库）
    try:
        cursor.execute('SELECT revoked_at FROM sessions LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE sessions ADD COLUMN revoked_at TIMESTAMP')

    # 创建API密钥表（用于MCP终端身份识别）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operate',
            user_id INTEGER REFERENCES users(id),
            is_disabled INTEGER DEFAULT 0,
            is_system INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP
        )
    ''')

    # 创建联系方表（供应商/客户）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT,
            phone TEXT,
            email TEXT,
            is_supplier INTEGER DEFAULT 0,
            is_customer INTEGER DEFAULT 0,
            notes TEXT,
            is_disabled INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 检查并添加 contact_id 字段到 inventory_records（用于已存在的数据库）
    try:
        cursor.execute('SELECT contact_id FROM inventory_records LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE inventory_records ADD COLUMN contact_id INTEGER REFERENCES contacts(id)')

    # 创建批次表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_no TEXT NOT NULL,
            material_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            initial_quantity INTEGER NOT NULL,
            contact_id INTEGER REFERENCES contacts(id),
            is_exhausted INTEGER DEFAULT 0,
            warehouse_id INTEGER REFERENCES warehouses(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials (id)
        )
    ''')

    # 创建批次消耗记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS batch_consumptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            batch_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            tenant_id INTEGER DEFAULT 1 REFERENCES tenants(id),
            warehouse_id INTEGER REFERENCES warehouses(id),
            FOREIGN KEY (record_id) REFERENCES inventory_records (id),
            FOREIGN KEY (batch_id) REFERENCES batches (id)
        )
    ''')

    # 检查并添加 batch_id 字段到 inventory_records（用于入库记录关联批次）
    try:
        cursor.execute('SELECT batch_id FROM inventory_records LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE inventory_records ADD COLUMN batch_id INTEGER REFERENCES batches(id)')

    # 检查并添加 operator_user_id 字段到 inventory_records（操作员关联用户表）
    try:
        cursor.execute('SELECT operator_user_id FROM inventory_records LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE inventory_records ADD COLUMN operator_user_id INTEGER REFERENCES users(id)')

    # 检查并添加 location 字段到 batches（批次级别存放位置）
    try:
        cursor.execute('SELECT location FROM batches LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE batches ADD COLUMN location TEXT')

    # 检查并添加 variant 字段到 batches（批次级别变体标识，如颜色/规格）
    try:
        cursor.execute('SELECT variant FROM batches LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE batches ADD COLUMN variant TEXT')

    # 注：历史上这里会从 materials.quantity 反向倒灌生成 LEGACY 批次。
    # 单一真相源后（batches.quantity 为权威，参见 get_material_quantity()），
    # 这种倒灌不再合理，已移除。历史数据修正走 Alembic 数据迁移。

    # 回填：将 materials.location 填入已有的无 location 批次
    cursor.execute('''
        UPDATE batches SET location = (
            SELECT m.location FROM materials m WHERE m.id = batches.material_id
        ) WHERE location IS NULL
    ''')

    # 创建系统设置KV表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 创建ERP Provider表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS erp_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            provider_name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            filename TEXT NOT NULL,
            config TEXT,
            test_results TEXT,
            test_passed_at TIMESTAMP,
            is_active INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 插入默认系统模式设置
    cursor.execute('''
        INSERT OR IGNORE INTO system_settings (key, value)
        VALUES ('system_mode', 'self_owned')
    ''')

    # 创建MCP连接表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mcp_connections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mcp_endpoint TEXT NOT NULL,
            api_key TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operate',
            auto_start INTEGER DEFAULT 1,
            status TEXT DEFAULT 'stopped',
            error_message TEXT,
            restart_count INTEGER DEFAULT 0,
            debug_mode INTEGER DEFAULT 0,
            device_id TEXT UNIQUE,
            created_at TEXT,
            updated_at TEXT
        )
    ''')

    # ============================================
    # 人脸识别 + 权限校验（Phase 1，仅对 MCP tool 生效）
    # ============================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tenant_face_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER UNIQUE NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            mode TEXT CHECK(mode IN('local','lan')),
            endpoint TEXT,
            auth_token TEXT,
            embedding_model_tag TEXT,
            min_confidence REAL NOT NULL DEFAULT 0.65,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS face_subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            employee_id TEXT,
            note TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_face_subjects_tenant ON face_subjects(tenant_id, is_active)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tenant_face_operation_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL,
            warehouse_id INTEGER,
            operation TEXT NOT NULL,
            require_face INTEGER NOT NULL DEFAULT 0,
            allowed_subject_ids TEXT,
            min_confidence_override REAL,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
            FOREIGN KEY(warehouse_id) REFERENCES warehouses(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_face_rules_lookup ON tenant_face_operation_rules(tenant_id, warehouse_id, operation)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS face_enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL,
            tenant_id INTEGER NOT NULL,
            model_tag TEXT NOT NULL,
            embedding BLOB NOT NULL,
            source_image_b64 TEXT,
            applies_to_warehouse_ids TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            enrolled_at TEXT DEFAULT CURRENT_TIMESTAMP,
            enrolled_by INTEGER,
            FOREIGN KEY(subject_id) REFERENCES face_subjects(id) ON DELETE CASCADE,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_face_enroll ON face_enrollments(tenant_id, model_tag, is_active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_face_enroll_subject ON face_enrollments(subject_id)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS face_auth_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            user_id INTEGER NOT NULL,
            matched_subject_id INTEGER,
            tenant_id INTEGER NOT NULL,
            warehouse_id INTEGER,
            operation TEXT NOT NULL,
            confidence REAL,
            decision TEXT NOT NULL CHECK(decision IN('pass','deny','skipped')),
            failure_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_face_logs_query ON face_auth_logs(tenant_id, created_at DESC)')

    # 为已有数据库添加新列
    try:
        cursor.execute('SELECT is_system FROM api_keys LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE api_keys ADD COLUMN is_system INTEGER DEFAULT 0')

    try:
        cursor.execute('SELECT role FROM mcp_connections LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE mcp_connections ADD COLUMN role TEXT DEFAULT \'operate\'')

    try:
        cursor.execute('SELECT debug_mode FROM mcp_connections LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE mcp_connections ADD COLUMN debug_mode INTEGER DEFAULT 0')

    try:
        cursor.execute('SELECT device_id FROM mcp_connections LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE mcp_connections ADD COLUMN device_id TEXT')
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_connections_device_id ON mcp_connections(device_id)')

    # 检查并添加 warehouse_id 字段到各表（多仓库支持）
    for table in ('batches', 'inventory_records', 'api_keys', 'mcp_connections'):
        try:
            cursor.execute(f'SELECT warehouse_id FROM {table} LIMIT 1')
        except sqlite3.OperationalError:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN warehouse_id INTEGER REFERENCES warehouses(id)')


    # 检查并添加 tenant_id 字段到各表（多租户支持）
    for table in ('warehouses', 'users', 'api_keys', 'mcp_connections', 'contacts', 'erp_providers', 'materials', 'batches', 'inventory_records'):
        try:
            cursor.execute(f'SELECT tenant_id FROM {table} LIMIT 1')
        except sqlite3.OperationalError:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN tenant_id INTEGER REFERENCES tenants(id) DEFAULT 1')

    # 检查并添加 warehouse_id 字段到 contacts
    try:
        cursor.execute('SELECT warehouse_id FROM contacts LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE contacts ADD COLUMN warehouse_id INTEGER REFERENCES warehouses(id)')


    # ============================================
    # 多租户迁移：回填 tenant_id 到默认租户
    # ============================================
    cursor.execute('UPDATE warehouses SET tenant_id = 1 WHERE tenant_id IS NULL')
    if get_deploy_mode() == 'multi_tenant':
        cursor.execute('SELECT COUNT(*) as cnt FROM users WHERE role = "admin" AND tenant_id IS NULL')
        has_global_admin = cursor.fetchone()['cnt'] > 0
        if not has_global_admin:
            cursor.execute('''
                UPDATE users SET tenant_id = NULL
                WHERE id = (
                    SELECT id FROM users
                    WHERE role = 'admin'
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                )
            ''')
        cursor.execute('UPDATE users SET tenant_id = 1 WHERE tenant_id IS NULL AND role != "admin"')
    else:
        cursor.execute('UPDATE users SET tenant_id = 1 WHERE tenant_id IS NULL')
    cursor.execute('UPDATE api_keys SET tenant_id = 1 WHERE tenant_id IS NULL')
    cursor.execute('UPDATE mcp_connections SET tenant_id = 1 WHERE tenant_id IS NULL')
    cursor.execute('UPDATE contacts SET tenant_id = 1 WHERE tenant_id IS NULL')
    cursor.execute('UPDATE erp_providers SET tenant_id = 1 WHERE tenant_id IS NULL')
    cursor.execute('UPDATE materials SET tenant_id = 1 WHERE tenant_id IS NULL')
    cursor.execute('UPDATE batches SET tenant_id = 1 WHERE tenant_id IS NULL')
    cursor.execute('UPDATE inventory_records SET tenant_id = 1 WHERE tenant_id IS NULL')

    # 修复历史数据中 tenant_id 与 warehouse_id 所属租户不一致的问题。
    # 这类数据会导致“写入成功但当前租户列表/看板查不到”。
    for table in ('materials', 'batches', 'inventory_records', 'api_keys', 'mcp_connections'):
        cursor.execute(f'''
            UPDATE {table}
            SET tenant_id = (
                SELECT w.tenant_id FROM warehouses w
                WHERE w.id = {table}.warehouse_id
            )
            WHERE warehouse_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM warehouses w
                WHERE w.id = {table}.warehouse_id
                  AND w.tenant_id IS NOT NULL
                  AND ({table}.tenant_id IS NULL OR {table}.tenant_id != w.tenant_id)
              )
        ''')

    # 联系方为租户级（不绑定仓库）：清空所有 contacts.warehouse_id
    cursor.execute('UPDATE contacts SET warehouse_id = NULL WHERE warehouse_id IS NOT NULL')

    # reason_category / reason_note 由 alembic initial schema 创建，旧的 reason 列已在
    # 迁移 c5d6e7f8a9b0 中删除。此处的 SQLite-only 兜底逻辑已无意义。

    # ============================================
    # 多仓库迁移：回填 warehouse_id 到默认仓库
    # ============================================
    cursor.execute('SELECT id FROM warehouses WHERE is_default = 1 LIMIT 1')
    default_wh = cursor.fetchone()
    if default_wh:
        default_wh_id = default_wh['id']
        cursor.execute('UPDATE materials SET warehouse_id = ? WHERE warehouse_id IS NULL', (default_wh_id,))
        cursor.execute('UPDATE batches SET warehouse_id = ? WHERE warehouse_id IS NULL', (default_wh_id,))
        cursor.execute('UPDATE inventory_records SET warehouse_id = ? WHERE warehouse_id IS NULL', (default_wh_id,))
        # api_keys 和 mcp_connections 的 warehouse_id 保持 NULL 表示全局

    # 多仓库索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_materials_warehouse ON materials(warehouse_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_batches_warehouse ON batches(warehouse_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_warehouse ON inventory_records(warehouse_id)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_materials_sku_wh ON materials(sku, warehouse_id)')

    # 多租户索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_warehouses_tenant ON warehouses(tenant_id)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_warehouses_slug_tenant ON warehouses(slug, tenant_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_tenant ON users(username, tenant_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_contacts_tenant ON contacts(tenant_id)')
    # 联系方已改为租户级，warehouse_id 始终为 NULL，不再建该索引
    cursor.execute('DROP INDEX IF EXISTS idx_contacts_warehouse')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_mcp_connections_tenant ON mcp_connections(tenant_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_materials_tenant ON materials(tenant_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_batches_tenant ON batches(tenant_id)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_batches_no_wh ON batches(batch_no, warehouse_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_tenant ON inventory_records(tenant_id)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_erp_providers_name_tenant ON erp_providers(provider_name, tenant_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bc_tenant ON batch_consumptions(tenant_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bc_warehouse ON batch_consumptions(warehouse_id)')

    # ============================================
    # DEPLOY_MODE 校验
    # ============================================
    if get_deploy_mode() == 'single_tenant':
        cursor.execute('SELECT COUNT(*) as cnt FROM tenants WHERE is_active = 1 AND id != 1')
        if cursor.fetchone()['cnt'] > 0:
            raise RuntimeError(
                'Cannot start in single_tenant mode: multiple active tenants exist. '
                'Switch to multi_tenant or consolidate tenants.'
            )
        # single_tenant 模式不允许存在全局 admin（tenant_id IS NULL）。该状态下"全局"概念不成立，
        # 留它会让 UI 各处判断分裂（一会儿 [全局管理] 一会儿租户内 admin）。要么切到 multi_tenant，
        # 要么把这些 admin 显式绑到租户。
        cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND tenant_id IS NULL")
        if cursor.fetchone()['cnt'] > 0:
            raise RuntimeError(
                'Cannot start in single_tenant mode: global admin (users.tenant_id IS NULL) exists. '
                'Either set DEPLOY_MODE=multi_tenant, or bind the admin to a tenant '
                '(UPDATE users SET tenant_id=1 WHERE role="admin" AND tenant_id IS NULL).'
            )

    conn.commit()
    conn.close()


def get_material_quantity(material_id: int) -> int:
    """单一真相源：从 batches 聚合得到 material 的当前库存量。

    返回 SUM(quantity) WHERE material_id=:mid AND is_exhausted=0（active 批次）。
    不读 materials.quantity（该字段视为派生缓存，仅供历史兼容）。
    """
    from sqlalchemy import select, func as _sa_func, and_ as _and
    from db import get_engine
    from metadata import batches as _t_batches
    with get_engine().connect() as conn:
        stmt = select(
            _sa_func.coalesce(_sa_func.sum(_t_batches.c.quantity), 0)
        ).where(_and(
            _t_batches.c.material_id == material_id,
            _t_batches.c.is_exhausted == 0,
        ))
        return int(conn.execute(stmt).scalar() or 0)


def get_materials_quantity_map(material_ids):
    """批量版本：一次性返回 {material_id: sum_active_batch_qty}。

    传入 list/iterable，未出现的 material_id 不会在返回 dict 中（调用方负责
    回退为 0）。空列表返回空 dict。
    """
    ids = list(material_ids) if material_ids is not None else []
    if not ids:
        return {}
    from sqlalchemy import select, func as _sa_func, and_ as _and
    from db import get_engine
    from metadata import batches as _t_batches
    with get_engine().connect() as conn:
        stmt = (
            select(
                _t_batches.c.material_id,
                _sa_func.coalesce(_sa_func.sum(_t_batches.c.quantity), 0).label("qty"),
            )
            .where(_and(
                _t_batches.c.material_id.in_(ids),
                _t_batches.c.is_exhausted == 0,
            ))
            .group_by(_t_batches.c.material_id)
        )
        return {int(r.material_id): int(r.qty or 0) for r in conn.execute(stmt).fetchall()}


def generate_batch_no(material_id: int, warehouse_id: Optional[int] = None, cursor=None) -> str:
    """生成批次号: YYYYMMDD-XXX (warehouse-scoped unique)

    传入 cursor 可在同一事务内看到未提交的批次（避免批量创建时序号冲突）。
    无 cursor 时：dialect-portable 路径走 SA Core。
    warehouse_id 用于隔离多仓库场景下的批次号序列。
    """
    today = datetime.now().strftime('%Y%m%d')
    like_pat = f'{today}-%'

    if cursor is not None:
        cursor.execute(
            "SELECT batch_no FROM batches WHERE batch_no LIKE ? AND (warehouse_id = ? OR ? IS NULL) ORDER BY batch_no DESC LIMIT 1",
            (like_pat, warehouse_id, warehouse_id),
        )
        row = cursor.fetchone()
        last_no = row['batch_no'] if row else None
    else:
        from sqlalchemy import select, or_, and_
        from db import get_engine
        from metadata import batches as _t_batches
        with get_engine().connect() as conn:
            row = conn.execute(
                select(_t_batches.c.batch_no)
                .where(and_(
                    _t_batches.c.batch_no.like(like_pat),
                    or_(_t_batches.c.warehouse_id == warehouse_id, warehouse_id is None),
                ))
                .order_by(_t_batches.c.batch_no.desc())
                .limit(1)
            ).first()
        last_no = row[0] if row else None

    if last_no:
        try:
            last_seq = int(last_no.split('-')[-1])
        except (ValueError, IndexError):
            last_seq = 0
        seq = last_seq + 1
    else:
        seq = 1
    return f'{today}-{seq:03d}'


def has_admin_user():
    """检查是否存在管理员用户。SA Core 实现，dialect-portable。"""
    from sqlalchemy import select, func
    from db import get_engine
    from metadata import users as _t_users
    with get_engine().connect() as conn:
        n = conn.execute(
            select(func.count()).select_from(_t_users).where(
                (_t_users.c.role == RoleName.ADMIN.value) & (_t_users.c.is_disabled == 0)
            )
        ).scalar() or 0
    return n > 0


def hash_password(password: str) -> str:
    """
    哈希密码
    - 启用bcrypt时使用bcrypt（推荐生产环境）
    - 否则使用SHA256+salt（向后兼容）
    """
    if BCRYPT_ENABLED:
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    else:
        # 旧版SHA256哈希（向后兼容）
        salt = "warehouse_system_salt_2024"
        return hashlib.sha256((password + salt).encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """
    验证密码
    自动识别bcrypt和SHA256格式，实现向后兼容
    """
    # bcrypt哈希以 $2b$ 或 $2a$ 开头
    if password_hash.startswith('$2'):
        if not BCRYPT_AVAILABLE:
            return False
        try:
            return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
        except Exception:
            return False
    else:
        # 旧版SHA256验证
        salt = "warehouse_system_salt_2024"
        return hashlib.sha256((password + salt).encode()).hexdigest() == password_hash


def needs_password_rehash(password_hash: str) -> bool:
    """
    检查密码是否需要升级到bcrypt
    用于登录时透明迁移旧密码
    """
    if not BCRYPT_ENABLED:
        return False
    # 如果不是bcrypt格式，需要重新哈希
    return not password_hash.startswith('$2')


def generate_session_token() -> str:
    """生成会话令牌"""
    return secrets.token_hex(32)


def generate_api_key() -> str:
    """生成API密钥（返回明文，只显示一次）"""
    return "wh_" + secrets.token_hex(24)


def hash_api_key(api_key: str) -> str:
    """
    哈希API密钥
    添加盐值提高安全性
    """
    salt = "warehouse_api_salt_2024"
    return hashlib.sha256(f"{api_key}:{salt}".encode()).hexdigest()


def generate_mock_data():
    """生成模拟数据（仅 SQLite，开发/演示用）。

    非 sqlite 方言下不执行 — 生产 MySQL 不应该被开发期 mock data 污染。
    """
    if not _is_sqlite():
        return
    conn = get_db_connection()
    cursor = conn.cursor()

    # 检查是否已有数据
    cursor.execute('SELECT COUNT(*) as count FROM materials')
    if cursor.fetchone()['count'] > 0:
        conn.close()
        return

    # 物料数据
    materials_data = [
        # 主板类
        ('watcher-xiaozhi主控板', 'MB-WZ-001', '主板类', 95, '个', 30, 'A区-01'),
        ('watcher-xiaozhi扩展板', 'MB-WZ-002', '主板类', 78, '个', 25, 'A区-02'),
        ('电源管理板', 'MB-PM-001', '主板类', 120, '个', 40, 'A区-03'),
        ('调试板', 'MB-DBG-001', '主板类', 45, '个', 15, 'A区-04'),

        # 传感器类
        ('高清摄像头模块', 'SN-CAM-001', '传感器类', 88, '个', 30, 'B区-01'),
        ('MEMS麦克风', 'SN-MIC-001', '传感器类', 150, '个', 50, 'B区-02'),
        ('PIR人体传感器', 'SN-PIR-001', '传感器类', 65, '个', 20, 'B区-03'),
        ('温湿度传感器', 'SN-TH-001', '传感器类', 92, '个', 30, 'B区-04'),
        ('光线传感器', 'SN-LUX-001', '传感器类', 55, '个', 20, 'B区-05'),
        ('陀螺仪模块', 'SN-GYRO-001', '传感器类', 38, '个', 15, 'B区-06'),

        # 外壳配件类
        ('watcher-xiaozhi外壳(上)', 'CS-WZ-001', '外壳配件类', 102, '个', 40, 'C区-01'),
        ('watcher-xiaozhi外壳(下)', 'CS-WZ-002', '外壳配件类', 98, '个', 40, 'C区-02'),
        ('万向支架', 'CS-BRK-001', '外壳配件类', 110, '个', 35, 'C区-03'),
        ('防水圈', 'CS-GSK-001', '外壳配件类', 145, '个', 50, 'C区-04'),
        ('散热片', 'CS-HS-001', '外壳配件类', 88, '个', 30, 'C区-05'),
        ('M3螺丝包(20pcs)', 'CS-SCR-M3', '外壳配件类', 156, '包', 60, 'C区-06'),
        ('M2螺丝包(20pcs)', 'CS-SCR-M2', '外壳配件类', 134, '包', 50, 'C区-07'),

        # 线材类
        ('USB-C数据线(1m)', 'CB-UC-1M', '线材类', 125, '条', 50, 'D区-01'),
        ('电源线(2m)', 'CB-PWR-2M', '线材类', 98, '条', 40, 'D区-02'),
        ('FPC排线(10cm)', 'CB-FPC-10', '线材类', 76, '条', 30, 'D区-03'),
        ('杜邦线(10p)', 'CB-DPN-10', '线材类', 89, '包', 30, 'D区-04'),

        # 包装类
        ('产品包装盒', 'PK-BOX-001', '包装类', 115, '个', 50, 'E区-01'),
        ('说明书', 'PK-MAN-001', '包装类', 128, '份', 60, 'E区-02'),
        ('保修卡', 'PK-WRT-001', '包装类', 135, '张', 60, 'E区-03'),
        ('合格证', 'PK-QC-001', '包装类', 142, '张', 60, 'E区-04'),
        ('防静电袋', 'PK-ESD-001', '包装类', 168, '个', 80, 'E区-05'),
        ('泡棉内衬', 'PK-FOM-001', '包装类', 95, '个', 40, 'E区-06'),

        # 电源类
        ('5V/3A电源适配器', 'PW-ADP-5V3A', '电源类', 82, '个', 30, 'F区-01'),
        ('12V/2A电源适配器', 'PW-ADP-12V2A', '电源类', 56, '个', 20, 'F区-02'),
        ('锂电池(3000mAh)', 'PW-BAT-3000', '电源类', 42, '个', 15, 'F区-03'),

        # 辅料类
        ('导热硅胶', 'AC-THP-001', '辅料类', 25, '支', 10, 'G区-01'),
        ('绝缘胶带', 'AC-TAPE-001', '辅料类', 38, '卷', 15, 'G区-02'),
        ('清洁布', 'AC-CLN-001', '辅料类', 92, '包', 30, 'G区-03'),

        # 成品
        ('watcher-xiaozhi整机', 'FG-WZ-001', '成品', 86, '台', 20, 'H区-01'),
        ('watcher-xiaozhi(标准版)', 'FG-WZ-STD', '成品', 52, '台', 15, 'H区-02'),
        ('watcher-xiaozhi(专业版)', 'FG-WZ-PRO', '成品', 34, '台', 10, 'H区-03'),
    ]

    # 获取默认仓库ID
    cursor.execute('SELECT id FROM warehouses WHERE is_default = 1 LIMIT 1')
    default_wh = cursor.fetchone()
    default_wh_id = default_wh['id'] if default_wh else 1

    # 插入物料数据
    for material in materials_data:
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (*material, default_wh_id))

    # 生成出入库记录（近7天）
    material_ids = [row[0] for row in cursor.execute('SELECT id FROM materials').fetchall()]

    # 带备注的分类演示数据：(category, [possible notes])
    reasons_in = [
        ('purchase', ['深圳供应商', '月度补货', '紧急采购', None]),
        ('return', ['张三归还', '李四归还', '研发部归还', None]),
        ('refund', ['质量问题退货', '客户退回', None]),
        ('produce', ['本周生产批次', None]),
        ('transfer_in', ['从B仓调入', None]),
        ('other_in', ['盘盈', None]),
    ]
    reasons_out = [
        ('sell', ['客户A订单', '线上订单#2024', '批发出货', None]),
        ('lend', ['借给张三，预计下周归还', '借给研发部测试', '借给李四', None]),
        ('consume', ['研发测试用', '产线领料', '日常消耗', None]),
        ('loss', ['运输破损', '仓库盘亏', None]),
        ('transfer_out', ['调拨至B仓', None]),
        ('other_out', ['报废处理', None]),
    ]
    operators = ['张三', '李四', '王五', '赵六', '系统']

    def _pick_reason(record_type):
        pool = reasons_in if record_type == RecordType.IN.value else reasons_out
        category, notes = random.choice(pool)
        note = random.choice(notes)
        return category, note

    # 生成过去7天的记录
    for day_offset in range(7, 0, -1):
        record_date = datetime.now() - timedelta(days=day_offset)
        # 每天5-15条记录
        num_records = random.randint(5, 15)

        for _ in range(num_records):
            material_id = random.choice(material_ids)
            record_type = random.choice([RecordType.IN.value, RecordType.OUT.value])
            quantity = random.randint(5, 30)
            operator = random.choice(operators)
            reason_category, reason_note = _pick_reason(record_type)

            # 随机时间（当天的某个时间）
            hour = random.randint(8, 18)
            minute = random.randint(0, 59)
            record_time = record_date.replace(hour=hour, minute=minute)

            cursor.execute('''
                INSERT INTO inventory_records (material_id, type, quantity, operator, reason_category, reason_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (material_id, record_type, quantity, operator, reason_category, reason_note, record_time.strftime('%Y-%m-%d %H:%M:%S')))

    # 生成今天的记录（更多一些）
    today = datetime.now()
    num_today_records = random.randint(15, 25)

    for _ in range(num_today_records):
        material_id = random.choice(material_ids)
        record_type = random.choice([RecordType.IN.value, RecordType.OUT.value])
        quantity = random.randint(5, 30)
        operator = random.choice(operators)
        reason_category, reason_note = _pick_reason(record_type)

        # 今天的随机时间
        hour = random.randint(8, datetime.now().hour if datetime.now().hour > 8 else 9)
        minute = random.randint(0, 59)
        record_time = today.replace(hour=hour, minute=minute)

        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, reason_category, reason_note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (material_id, record_type, quantity, operator, reason_category, reason_note, record_time.strftime('%Y-%m-%d %H:%M:%S')))

    conn.commit()
    conn.close()



def get_deploy_mode() -> str:
    return os.environ.get('DEPLOY_MODE', 'single_tenant')

if __name__ == '__main__':
    init_database()
    generate_mock_data()
    print("数据库初始化完成！")
