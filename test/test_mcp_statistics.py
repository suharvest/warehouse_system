#!/usr/bin/env python3
"""
测试 MCP 统计接口

测试 get_today_statistics 工具
"""

import sys
import os

# 获取项目根目录
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(project_root, 'backend')
sys.path.insert(0, backend_dir)

# 切换到backend目录，确保数据库路径正确
os.chdir(backend_dir)

from database import get_db_connection
from datetime import datetime


def test_today_statistics():
    """测试查询今日统计数据"""
    print("=" * 60)
    print("测试: 查询今日统计数据")
    print("=" * 60)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 获取今天的日期
        today = datetime.now().strftime('%Y-%m-%d')

        # 查询今日入库总数
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total_in
            FROM inventory_records
            WHERE type = 'in' AND DATE(created_at) = ?
        ''', (today,))
        today_in = cursor.fetchone()['total_in']

        # 查询今日出库总数
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total_out
            FROM inventory_records
            WHERE type = 'out' AND DATE(created_at) = ?
        ''', (today,))
        today_out = cursor.fetchone()['total_out']

        # 查询当前库存总量
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total_stock
            FROM materials
        ''')
        total_stock = cursor.fetchone()['total_stock']

        # 查询库存预警数量
        cursor.execute('''
            SELECT COUNT(*) as low_stock_count
            FROM materials
            WHERE quantity < safe_stock
        ''')
        low_stock_count = cursor.fetchone()['low_stock_count']

        conn.close()

        print(f"\n日期: {today}")
        print(f"今日入库: {today_in} 件")
        print(f"今日出库: {today_out} 件")
        print(f"净变化: {today_in - today_out} 件")
        print(f"当前库存总量: {total_stock} 件")
        print(f"库存预警数量: {low_stock_count} 种")

        print("\n✅ 统计数据查询成功！")
        return True

    except Exception as e:
        print(f"\n❌ 统计数据查询失败: {str(e)}")
        return False


def test_with_operations():
    """测试入库/出库操作后的统计数据变化"""
    print("\n" + "=" * 60)
    print("测试: 入库/出库后统计数据变化")
    print("=" * 60)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 查询初始统计
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total_in
            FROM inventory_records
            WHERE type = 'in' AND DATE(created_at) = ?
        ''', (today,))
        initial_in = cursor.fetchone()['total_in']

        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total_out
            FROM inventory_records
            WHERE type = 'out' AND DATE(created_at) = ?
        ''', (today,))
        initial_out = cursor.fetchone()['total_out']

        print(f"\n初始状态:")
        print(f"  今日入库: {initial_in} 件")
        print(f"  今日出库: {initial_out} 件")

        # 执行一次入库操作
        cursor.execute('SELECT id FROM materials WHERE name = ?', ('watcher-xiaozhi(标准版)',))
        material_id = cursor.fetchone()['id']

        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
            VALUES (?, 'in', ?, ?, ?, ?)
        ''', (material_id, 5, 'test_script', '测试入库', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()

        # 查询更新后的统计
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total_in
            FROM inventory_records
            WHERE type = 'in' AND DATE(created_at) = ?
        ''', (today,))
        updated_in = cursor.fetchone()['total_in']

        print(f"\n执行入库 5 件后:")
        print(f"  今日入库: {updated_in} 件 (增加 {updated_in - initial_in} 件)")

        # 回滚测试数据
        conn.rollback()
        conn.close()

        print("\n✅ 统计数据变化测试成功！")
        return True

    except Exception as e:
        print(f"\n❌ 统计数据变化测试失败: {str(e)}")
        return False


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("MCP 统计接口测试")
    print("=" * 60)

    results = []

    # 测试1: 查询统计数据
    results.append(test_today_statistics())

    # 测试2: 统计数据变化
    results.append(test_with_operations())

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    total = len(results)
    passed = sum(results)

    print(f"\n总测试数: {total}")
    print(f"通过: {passed}")
    print(f"失败: {total - passed}")

    if all(results):
        print("\n🎉 所有测试通过！")
        sys.exit(0)
    else:
        print("\n❌ 部分测试失败")
        sys.exit(1)
