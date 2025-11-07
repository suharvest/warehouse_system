#!/usr/bin/env python3
"""
测试 MCP 工具
"""

import sys
import os
import json

# 获取项目根目录
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(project_root, 'backend')

# 添加路径并切换到backend目录
sys.path.insert(0, backend_dir)
os.chdir(backend_dir)

from database import get_db_connection
from datetime import datetime


def print_result(title, result):
    """打印结果"""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()


def query_stock(product_name):
    """查询库存"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM materials WHERE name = ?', (product_name,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'success': True,
            'product': {
                'name': row['name'],
                'sku': row['sku'],
                'quantity': row['quantity'],
                'unit': row['unit'],
                'safe_stock': row['safe_stock'],
                'location': row['location']
            }
        }
    return {'success': False, 'error': '产品不存在'}


def do_stock_in(product_name, quantity):
    """入库"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, quantity, unit FROM materials WHERE name = ?', (product_name,))
    row = cursor.fetchone()
    if row:
        new_qty = row['quantity'] + quantity
        cursor.execute('UPDATE materials SET quantity = ? WHERE id = ?', (new_qty, row['id']))
        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
            VALUES (?, 'in', ?, ?, ?, ?)
        ''', (row['id'], quantity, '测试脚本', '测试入库', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()
        return {'success': True, 'new_quantity': new_qty, 'unit': row['unit']}
    conn.close()
    return {'success': False, 'error': '产品不存在'}


def do_stock_out(product_name, quantity):
    """出库"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, quantity, unit FROM materials WHERE name = ?', (product_name,))
    row = cursor.fetchone()
    if row:
        if row['quantity'] < quantity:
            conn.close()
            return {'success': False, 'error': '库存不足'}
        new_qty = row['quantity'] - quantity
        cursor.execute('UPDATE materials SET quantity = ? WHERE id = ?', (new_qty, row['id']))
        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
            VALUES (?, 'out', ?, ?, ?, ?)
        ''', (row['id'], quantity, '测试脚本', '测试出库', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()
        return {'success': True, 'new_quantity': new_qty, 'unit': row['unit']}
    conn.close()
    return {'success': False, 'error': '产品不存在'}


def main():
    print("\n仓库管理系统 MCP 工具测试")
    print("=" * 60)

    product_name = "watcher-xiaozhi(标准版)"

    # 1. 查询标准版库存
    print("\n1. 测试: 查询 watcher-xiaozhi(标准版) 库存")
    result = query_stock(product_name)
    print_result("查询库存", result)

    if result['success']:
        old_quantity = result['product']['quantity']
        print(f"当前库存: {old_quantity} 台")

        # 2. 入库操作
        print("\n2. 测试: 入库 10 台")
        result = do_stock_in(product_name, 10)
        print_result("入库操作", result)

        # 3. 再次查询验证
        print("\n3. 测试: 验证入库后的库存")
        result = query_stock(product_name)
        print_result("查询库存", result)

        if result['success']:
            new_quantity = result['product']['quantity']
            print(f"更新后库存: {new_quantity} 台")
            print(f"预期库存: {old_quantity + 10} 台")
            print(f"验证结果: {'✅ 通过' if new_quantity == old_quantity + 10 else '❌ 失败'}")

        # 4. 出库操作
        print("\n4. 测试: 出库 5 台")
        result = do_stock_out(product_name, 5)
        print_result("出库操作", result)

        # 5. 最终查询
        print("\n5. 测试: 验证出库后的库存")
        result = query_stock(product_name)
        print_result("查询库存", result)

        if result['success']:
            final_quantity = result['product']['quantity']
            expected = old_quantity + 10 - 5
            print(f"最终库存: {final_quantity} 台")
            print(f"预期库存: {expected} 台")
            print(f"验证结果: {'✅ 通过' if final_quantity == expected else '❌ 失败'}")

    # 6. 测试错误情况 - 库存不足
    print("\n6. 测试: 出库数量超过库存")
    result = do_stock_out(product_name, 99999)
    print_result("错误处理 - 库存不足", result)

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)
    print("\n提示：请访问 http://localhost:2125 查看前端界面的实时更新")
    print("（库存列表会在2秒内自动刷新显示最新数据）\n")


if __name__ == "__main__":
    main()
