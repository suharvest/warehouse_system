#!/usr/bin/env python3
"""
Smart WMS Demo Database Initializer
Creates demo database with:
- Only one user: seeed (password: seeed, role: admin)
- 36 types of materials
- 7 days of inventory records
"""

import sqlite3
import os
import hashlib
import random
from datetime import datetime, timedelta

# 尝试导入bcrypt
try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False
    print("Warning: bcrypt not available, using SHA256 for password hashing")

# 数据库路径
DATABASE_PATH = os.environ.get('DATABASE_PATH', '/opt/smart_wms/data/warehouse.db')


def hash_password(password: str) -> str:
    """哈希密码"""
    if BCRYPT_AVAILABLE:
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    else:
        salt = "warehouse_system_salt_2024"
        return hashlib.sha256((password + salt).encode()).hexdigest()


def init_database():
    """初始化数据库表结构"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # 创建物料表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sku TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            unit TEXT DEFAULT '个',
            safe_stock INTEGER DEFAULT 20,
            location TEXT,
            is_disabled INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 创建出入库记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            operator TEXT DEFAULT '系统',
            operator_user_id INTEGER REFERENCES users(id),
            reason TEXT,
            contact_id INTEGER REFERENCES contacts(id),
            batch_id INTEGER REFERENCES batches(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials (id)
        )
    ''')

    # 创建用户表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'view',
            display_name TEXT,
            is_disabled INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER REFERENCES users(id)
        )
    ''')

    # 创建会话表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # 创建API密钥表
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

    # 创建联系方表
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

    # 创建批次表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_no TEXT UNIQUE NOT NULL,
            material_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            initial_quantity INTEGER NOT NULL,
            contact_id INTEGER REFERENCES contacts(id),
            is_exhausted INTEGER DEFAULT 0,
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
            FOREIGN KEY (record_id) REFERENCES inventory_records (id),
            FOREIGN KEY (batch_id) REFERENCES batches (id)
        )
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
            created_at TEXT,
            updated_at TEXT
        )
    ''')

    conn.commit()
    conn.close()
    print(f"Database schema initialized: {DATABASE_PATH}")


def create_seeed_user():
    """创建 seeed 用户 (admin)"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # 检查是否已存在
    cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'seeed'")
    if cursor.fetchone()[0] > 0:
        print("User 'seeed' already exists, skipping...")
        conn.close()
        return

    # 创建 seeed 用户
    password_hash = hash_password('seeed')
    cursor.execute('''
        INSERT INTO users (username, password_hash, role, display_name)
        VALUES (?, ?, ?, ?)
    ''', ('seeed', password_hash, 'admin', 'Seeed Studio'))

    conn.commit()
    conn.close()
    print("Created user: seeed (password: seeed, role: admin)")


def generate_mock_data():
    """生成模拟数据"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # 检查是否已有数据
    cursor.execute('SELECT COUNT(*) as count FROM materials')
    if cursor.fetchone()[0] > 0:
        print("Materials already exist, skipping mock data generation...")
        conn.close()
        return

    # 物料数据 (36种)
    materials_data = [
        # 主板类 (4)
        ('watcher-xiaozhi主控板', 'MB-WZ-001', '主板类', 95, '个', 30, 'A区-01'),
        ('watcher-xiaozhi扩展板', 'MB-WZ-002', '主板类', 78, '个', 25, 'A区-02'),
        ('电源管理板', 'MB-PM-001', '主板类', 120, '个', 40, 'A区-03'),
        ('调试板', 'MB-DBG-001', '主板类', 45, '个', 15, 'A区-04'),

        # 传感器类 (6)
        ('高清摄像头模块', 'SN-CAM-001', '传感器类', 88, '个', 30, 'B区-01'),
        ('MEMS麦克风', 'SN-MIC-001', '传感器类', 150, '个', 50, 'B区-02'),
        ('PIR人体传感器', 'SN-PIR-001', '传感器类', 65, '个', 20, 'B区-03'),
        ('温湿度传感器', 'SN-TH-001', '传感器类', 92, '个', 30, 'B区-04'),
        ('光线传感器', 'SN-LUX-001', '传感器类', 55, '个', 20, 'B区-05'),
        ('陀螺仪模块', 'SN-GYRO-001', '传感器类', 38, '个', 15, 'B区-06'),

        # 外壳配件类 (7)
        ('watcher-xiaozhi外壳(上)', 'CS-WZ-001', '外壳配件类', 102, '个', 40, 'C区-01'),
        ('watcher-xiaozhi外壳(下)', 'CS-WZ-002', '外壳配件类', 98, '个', 40, 'C区-02'),
        ('万向支架', 'CS-BRK-001', '外壳配件类', 110, '个', 35, 'C区-03'),
        ('防水圈', 'CS-GSK-001', '外壳配件类', 145, '个', 50, 'C区-04'),
        ('散热片', 'CS-HS-001', '外壳配件类', 88, '个', 30, 'C区-05'),
        ('M3螺丝包(20pcs)', 'CS-SCR-M3', '外壳配件类', 156, '包', 60, 'C区-06'),
        ('M2螺丝包(20pcs)', 'CS-SCR-M2', '外壳配件类', 134, '包', 50, 'C区-07'),

        # 线材类 (4)
        ('USB-C数据线(1m)', 'CB-UC-1M', '线材类', 125, '条', 50, 'D区-01'),
        ('电源线(2m)', 'CB-PWR-2M', '线材类', 98, '条', 40, 'D区-02'),
        ('FPC排线(10cm)', 'CB-FPC-10', '线材类', 76, '条', 30, 'D区-03'),
        ('杜邦线(10p)', 'CB-DPN-10', '线材类', 89, '包', 30, 'D区-04'),

        # 包装类 (6)
        ('产品包装盒', 'PK-BOX-001', '包装类', 115, '个', 50, 'E区-01'),
        ('说明书', 'PK-MAN-001', '包装类', 128, '份', 60, 'E区-02'),
        ('保修卡', 'PK-WRT-001', '包装类', 135, '张', 60, 'E区-03'),
        ('合格证', 'PK-QC-001', '包装类', 142, '张', 60, 'E区-04'),
        ('防静电袋', 'PK-ESD-001', '包装类', 168, '个', 80, 'E区-05'),
        ('泡棉内衬', 'PK-FOM-001', '包装类', 95, '个', 40, 'E区-06'),

        # 电源类 (3)
        ('5V/3A电源适配器', 'PW-ADP-5V3A', '电源类', 82, '个', 30, 'F区-01'),
        ('12V/2A电源适配器', 'PW-ADP-12V2A', '电源类', 56, '个', 20, 'F区-02'),
        ('锂电池(3000mAh)', 'PW-BAT-3000', '电源类', 42, '个', 15, 'F区-03'),

        # 辅料类 (3)
        ('导热硅胶', 'AC-THP-001', '辅料类', 25, '支', 10, 'G区-01'),
        ('绝缘胶带', 'AC-TAPE-001', '辅料类', 38, '卷', 15, 'G区-02'),
        ('清洁布', 'AC-CLN-001', '辅料类', 92, '包', 30, 'G区-03'),

        # 成品 (3)
        ('watcher-xiaozhi整机', 'FG-WZ-001', '成品', 86, '台', 20, 'H区-01'),
        ('watcher-xiaozhi(标准版)', 'FG-WZ-STD', '成品', 52, '台', 15, 'H区-02'),
        ('watcher-xiaozhi(专业版)', 'FG-WZ-PRO', '成品', 34, '台', 10, 'H区-03'),
    ]

    # 插入物料数据
    for material in materials_data:
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', material)

    print(f"Created {len(materials_data)} materials")

    # 获取物料ID
    material_ids = [row[0] for row in cursor.execute('SELECT id FROM materials').fetchall()]

    # 生成出入库记录
    reasons_in = ['采购入库', '生产完工入库', '退货入库', '调拨入库']
    reasons_out = ['生产领料', '销售出库', '研发领用', '调拨出库', '返修出库']
    operators = ['张三', '李四', '王五', '赵六', 'seeed']

    total_records = 0

    # 生成过去7天的记录
    for day_offset in range(7, 0, -1):
        record_date = datetime.now() - timedelta(days=day_offset)
        num_records = random.randint(5, 15)

        for _ in range(num_records):
            material_id = random.choice(material_ids)
            record_type = random.choice(['in', 'out'])
            quantity = random.randint(5, 30)
            operator = random.choice(operators)

            if record_type == 'in':
                reason = random.choice(reasons_in)
            else:
                reason = random.choice(reasons_out)

            hour = random.randint(8, 18)
            minute = random.randint(0, 59)
            record_time = record_date.replace(hour=hour, minute=minute)

            cursor.execute('''
                INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (material_id, record_type, quantity, operator, reason, record_time.strftime('%Y-%m-%d %H:%M:%S')))
            total_records += 1

    # 生成今天的记录
    today = datetime.now()
    num_today_records = random.randint(15, 25)

    for _ in range(num_today_records):
        material_id = random.choice(material_ids)
        record_type = random.choice(['in', 'out'])
        quantity = random.randint(5, 30)
        operator = random.choice(operators)

        if record_type == 'in':
            reason = random.choice(reasons_in)
        else:
            reason = random.choice(reasons_out)

        hour = random.randint(8, min(datetime.now().hour, 18) if datetime.now().hour > 8 else 9)
        minute = random.randint(0, 59)
        record_time = today.replace(hour=hour, minute=minute)

        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (material_id, record_type, quantity, operator, reason, record_time.strftime('%Y-%m-%d %H:%M:%S')))
        total_records += 1

    conn.commit()
    conn.close()
    print(f"Created {total_records} inventory records (7 days)")


def main():
    """主函数"""
    print("=" * 50)
    print("Smart WMS Demo Database Initializer")
    print("=" * 50)

    # 确保目录存在
    db_dir = os.path.dirname(DATABASE_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        print(f"Created directory: {db_dir}")

    # 删除旧数据库（如果存在）
    if os.path.exists(DATABASE_PATH):
        os.remove(DATABASE_PATH)
        print(f"Removed old database: {DATABASE_PATH}")

    # 初始化数据库
    init_database()

    # 创建 seeed 用户
    create_seeed_user()

    # 生成模拟数据
    generate_mock_data()

    print("=" * 50)
    print("Demo database initialized successfully!")
    print(f"Database: {DATABASE_PATH}")
    print("Login: seeed / seeed")
    print("=" * 50)


if __name__ == '__main__':
    main()
