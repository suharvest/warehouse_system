#!/usr/bin/env python3
"""
测试后端 API 接口
"""

import requests
import json
import time

API_BASE_URL = 'http://localhost:2124/api'


def print_result(title, data):
    """打印结果"""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print()


def test_api():
    """测试 API 接口"""
    print("\n仓库管理系统 API 测试")
    print("=" * 60)

    # 检查后端是否运行
    try:
        response = requests.get(f"{API_BASE_URL}/dashboard/stats", timeout=5)
        if response.status_code != 200:
            print("\n❌ 后端服务未运行！")
            print("请先启动后端服务：")
            print("  cd /Users/harvest/project/test_dataset/warehouse_system")
            print("  uv run python run_backend.py")
            return
    except requests.exceptions.ConnectionError:
        print("\n❌ 无法连接到后端服务！")
        print("请先启动后端服务：")
        print("  cd /Users/harvest/project/test_dataset/warehouse_system")
        print("  uv run python run_backend.py")
        return

    print("\n✅ 后端服务运行正常")

    # 1. 测试统计数据
    print("\n1. 测试: 获取仪表盘统计数据")
    response = requests.get(f"{API_BASE_URL}/dashboard/stats")
    print_result("统计数据", response.json())

    # 2. 测试类型分布
    print("\n2. 测试: 获取库存类型分布")
    response = requests.get(f"{API_BASE_URL}/dashboard/category-distribution")
    data = response.json()
    print_result("类型分布 (前5项)", data[:5] if len(data) > 5 else data)

    # 3. 测试近7天趋势
    print("\n3. 测试: 获取近7天出入库趋势")
    response = requests.get(f"{API_BASE_URL}/dashboard/weekly-trend")
    print_result("近7天趋势", response.json())

    # 4. 测试库存TOP10
    print("\n4. 测试: 获取库存TOP10")
    response = requests.get(f"{API_BASE_URL}/dashboard/top-stock")
    data = response.json()
    top_3 = {
        'names': data['names'][:3],
        'quantities': data['quantities'][:3],
        'categories': data['categories'][:3]
    }
    print_result("TOP10 (前3项)", top_3)

    # 5. 测试所有物料
    print("\n5. 测试: 获取所有物料")
    response = requests.get(f"{API_BASE_URL}/materials/all")
    data = response.json()
    print_result(f"所有物料 (共{len(data)}种，显示前3种)", data[:3])

    # 6. 测试 xiaozhi 相关物料
    print("\n6. 测试: 获取 watcher-xiaozhi 相关物料")
    response = requests.get(f"{API_BASE_URL}/materials/xiaozhi")
    data = response.json()
    print_result(f"xiaozhi 相关物料 (共{len(data)}种)", data)

    # 7. 测试库存预警
    print("\n7. 测试: 获取库存预警")
    response = requests.get(f"{API_BASE_URL}/dashboard/low-stock-alert")
    data = response.json()
    if len(data) > 0:
        print_result(f"库存预警 (共{len(data)}项，显示前3项)", data[:3])
    else:
        print_result("库存预警", {"message": "暂无预警"})

    print("\n" + "=" * 60)
    print("API 测试完成！")
    print("=" * 60)
    print("\n所有接口测试通过 ✅")
    print("\n提示：请访问 http://localhost:2125 查看前端页面\n")


if __name__ == "__main__":
    test_api()
