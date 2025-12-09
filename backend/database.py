import sqlite3
import os
from datetime import datetime, timedelta
import random

# 支持通过环境变量配置数据库路径，默认为当前目录下的 warehouse.db
DATABASE_PATH = os.environ.get('DATABASE_PATH', 'warehouse.db')

def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """初始化数据库表结构"""
    conn = get_db_connection()
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

    # 检查并添加 is_disabled 字段（用于已存在的数据库）
    try:
        cursor.execute('SELECT is_disabled FROM materials LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE materials ADD COLUMN is_disabled INTEGER DEFAULT 0')

    # 创建出入库记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            operator TEXT DEFAULT '系统',
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES materials (id)
        )
    ''')

    conn.commit()
    conn.close()

def generate_mock_data():
    """生成模拟数据"""
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

    # 插入物料数据
    for material in materials_data:
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', material)

    # 生成出入库记录（近7天）
    material_ids = [row[0] for row in cursor.execute('SELECT id FROM materials').fetchall()]

    reasons_in = ['采购入库', '生产完工入库', '退货入库', '调拨入库']
    reasons_out = ['生产领料', '销售出库', '研发领用', '调拨出库', '返修出库']
    operators = ['张三', '李四', '王五', '赵六', '系统']

    # 生成过去7天的记录
    for day_offset in range(7, 0, -1):
        record_date = datetime.now() - timedelta(days=day_offset)
        # 每天5-15条记录
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

            # 随机时间（当天的某个时间）
            hour = random.randint(8, 18)
            minute = random.randint(0, 59)
            record_time = record_date.replace(hour=hour, minute=minute)

            cursor.execute('''
                INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (material_id, record_type, quantity, operator, reason, record_time.strftime('%Y-%m-%d %H:%M:%S')))

    # 生成今天的记录（更多一些）
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

        # 今天的随机时间
        hour = random.randint(8, datetime.now().hour if datetime.now().hour > 8 else 9)
        minute = random.randint(0, 59)
        record_time = today.replace(hour=hour, minute=minute)

        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (material_id, record_type, quantity, operator, reason, record_time.strftime('%Y-%m-%d %H:%M:%S')))

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_database()
    generate_mock_data()
    print("数据库初始化完成！")
