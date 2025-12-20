"""
数据迁移脚本：修复历史数据
1. 根据 operator 文本匹配用户，填充 operator_user_id
2. 为没有批次的入库记录创建批次
"""
import sqlite3
import os
from datetime import datetime

DATABASE_PATH = os.environ.get('DATABASE_PATH', 'warehouse.db')


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_operator_user_id():
    """根据 operator 文本匹配用户，填充 operator_user_id"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 获取所有用户
    cursor.execute('SELECT id, username, display_name FROM users')
    users = cursor.fetchall()

    if not users:
        print("没有用户数据，跳过 operator_user_id 迁移")
        conn.close()
        return

    # 构建匹配映射：operator文本 -> user_id
    operator_map = {}
    for user in users:
        # 匹配 username
        operator_map[user['username']] = user['id']
        # 匹配 display_name
        if user['display_name']:
            operator_map[user['display_name']] = user['id']

    print(f"用户映射: {operator_map}")

    # 查找需要更新的记录
    cursor.execute('''
        SELECT id, operator FROM inventory_records
        WHERE operator_user_id IS NULL AND operator IS NOT NULL
    ''')
    records = cursor.fetchall()

    updated_count = 0
    for record in records:
        operator = record['operator']
        if operator in operator_map:
            cursor.execute('''
                UPDATE inventory_records
                SET operator_user_id = ?
                WHERE id = ?
            ''', (operator_map[operator], record['id']))
            updated_count += 1

    conn.commit()
    print(f"已更新 {updated_count}/{len(records)} 条记录的 operator_user_id")
    conn.close()


def migrate_batches():
    """为没有批次的入库记录创建批次"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 查找没有 batch_id 的入库记录
    cursor.execute('''
        SELECT r.id, r.material_id, r.quantity, r.contact_id, r.created_at,
               m.name as material_name
        FROM inventory_records r
        JOIN materials m ON r.material_id = m.id
        WHERE r.type = 'in' AND r.batch_id IS NULL
        ORDER BY r.created_at ASC
    ''')
    records = cursor.fetchall()

    if not records:
        print("没有需要迁移的入库记录")
        conn.close()
        return

    print(f"找到 {len(records)} 条没有批次的入库记录")

    # 跟踪每个日期的批次计数
    date_counters = {}

    # 为每条入库记录创建批次
    for record in records:
        # 生成批次号
        created_at = record['created_at']
        if isinstance(created_at, str):
            try:
                dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                dt = datetime.now()
        else:
            dt = datetime.now()

        date_str = dt.strftime('%Y%m%d')

        # 初始化日期计数器（从数据库中已有的批次数开始）
        if date_str not in date_counters:
            cursor.execute('''
                SELECT COUNT(*) as count FROM batches
                WHERE batch_no LIKE ?
            ''', (f'{date_str}-%',))
            date_counters[date_str] = cursor.fetchone()['count']

        # 递增计数器
        date_counters[date_str] += 1
        batch_no = f"{date_str}-{date_counters[date_str]:03d}"

        # 创建批次
        cursor.execute('''
            INSERT INTO batches (batch_no, material_id, quantity, initial_quantity, contact_id, is_exhausted, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        ''', (
            batch_no,
            record['material_id'],
            record['quantity'],  # 假设批次未被消耗
            record['quantity'],
            record['contact_id'],
            record['created_at']
        ))
        batch_id = cursor.lastrowid

        # 更新入库记录的 batch_id
        cursor.execute('''
            UPDATE inventory_records SET batch_id = ? WHERE id = ?
        ''', (batch_id, record['id']))

        print(f"  创建批次 {batch_no} (物料: {record['material_name']}, 数量: {record['quantity']})")

    conn.commit()
    print(f"已为 {len(records)} 条入库记录创建批次")
    conn.close()


def show_data_status():
    """显示数据状态"""
    conn = get_db_connection()
    cursor = conn.cursor()

    print("\n=== 数据状态 ===")

    # 用户数量
    cursor.execute('SELECT COUNT(*) as count FROM users')
    print(f"用户数量: {cursor.fetchone()['count']}")

    # 联系方数量
    cursor.execute('SELECT COUNT(*) as count FROM contacts')
    print(f"联系方数量: {cursor.fetchone()['count']}")

    # 物料数量
    cursor.execute('SELECT COUNT(*) as count FROM materials')
    print(f"物料数量: {cursor.fetchone()['count']}")

    # 出入库记录
    cursor.execute('SELECT COUNT(*) as count FROM inventory_records')
    total_records = cursor.fetchone()['count']

    cursor.execute('SELECT COUNT(*) as count FROM inventory_records WHERE operator_user_id IS NOT NULL')
    with_user_id = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM inventory_records WHERE type = 'in'")
    in_records = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM inventory_records WHERE type = 'in' AND batch_id IS NOT NULL")
    with_batch = cursor.fetchone()['count']

    print(f"出入库记录: {total_records}")
    print(f"  - 有 operator_user_id: {with_user_id}/{total_records}")
    print(f"  - 入库记录有 batch_id: {with_batch}/{in_records}")

    # 批次数量
    cursor.execute('SELECT COUNT(*) as count FROM batches')
    print(f"批次数量: {cursor.fetchone()['count']}")

    conn.close()


if __name__ == '__main__':
    print("开始数据迁移...\n")

    show_data_status()

    print("\n--- 迁移 operator_user_id ---")
    migrate_operator_user_id()

    print("\n--- 迁移批次数据 ---")
    migrate_batches()

    print("\n")
    show_data_status()

    print("\n迁移完成!")
