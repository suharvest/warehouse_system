#!/usr/bin/env python3
"""
仓库管理系统 MCP 服务器

提供 watcher-xiaozhi 产品的库存管理功能：
- 查询库存
- 入库操作
- 出库操作
- 查询当天统计数据（入库、出库、库存总量）

注意：本服务通过调用后端 API 实现所有操作，不直接操作数据库。
确保后端服务（端口 2124）已启动后再使用 MCP 服务。
"""

from fastmcp import FastMCP
import sys
import logging
import requests

# 配置日志
logger = logging.getLogger('WarehouseMCP')

# 修复 Windows 控制台 UTF-8 编码
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

# 后端 API 地址
API_BASE_URL = "http://localhost:2124/api"

# 创建 MCP 服务器
mcp = FastMCP("Warehouse System")


def api_get(endpoint: str, params: dict = None) -> dict:
    """发送 GET 请求到后端 API"""
    try:
        response = requests.get(f"{API_BASE_URL}{endpoint}", params=params, timeout=10)
        return response.json()
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": "无法连接到后端服务",
            "message": "请确保后端服务（端口 2124）已启动"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"API 请求失败: {str(e)}"
        }


def api_post(endpoint: str, data: dict) -> dict:
    """发送 POST 请求到后端 API"""
    try:
        response = requests.post(f"{API_BASE_URL}{endpoint}", json=data, timeout=10)
        return response.json()
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": "无法连接到后端服务",
            "message": "请确保后端服务（端口 2124）已启动"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"API 请求失败: {str(e)}"
        }


@mcp.tool()
def query_xiaozhi_stock(product_name: str = "watcher-xiaozhi(标准版)") -> dict:
    """
    查询 watcher-xiaozhi 产品的库存信息

    参数:
        product_name: 产品名称，默认为 "watcher-xiaozhi(标准版)"
                     可选值：
                     - "watcher-xiaozhi(标准版)"
                     - "watcher-xiaozhi(专业版)"
                     - "watcher-xiaozhi整机"
                     - "watcher-xiaozhi主控板"
                     - "watcher-xiaozhi扩展板"
                     - "watcher-xiaozhi外壳(上)"
                     - "watcher-xiaozhi外壳(下)"

    返回:
        包含库存信息的字典
    """
    try:
        # 调用后端 API 获取产品统计数据
        data = api_get("/materials/product-stats", params={"name": product_name})

        # 检查是否有错误
        if "error" in data:
            logger.warning(f"产品不存在: {product_name}")
            return {
                "success": False,
                "error": data["error"],
                "message": f"产品 '{product_name}' 不存在，请检查产品名称"
            }

        # 判断库存状态
        quantity = data["current_stock"]
        safe_stock = data["safe_stock"]

        if quantity >= safe_stock:
            status = "正常"
        elif quantity >= safe_stock * 0.5:
            status = "偏低"
        else:
            status = "告急"

        result = {
            "success": True,
            "product": {
                "name": data["name"],
                "sku": data["sku"],
                "quantity": quantity,
                "unit": data["unit"],
                "safe_stock": safe_stock,
                "location": data["location"],
                "status": status
            },
            "message": f"查询成功：{data['name']} 当前库存 {quantity} {data['unit']}"
        }

        logger.info(f"查询库存: {product_name}, 数量: {quantity}")
        return result

    except Exception as e:
        logger.error(f"查询库存失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "message": f"查询失败: {str(e)}"
        }


@mcp.tool()
def stock_in(product_name: str, quantity: int, reason: str = "采购入库", operator: str = "MCP系统") -> dict:
    """
    watcher-xiaozhi 产品入库操作

    参数:
        product_name: 产品名称（必填）
        quantity: 入库数量（必填，必须大于0）
        reason: 入库原因，默认为"采购入库"
        operator: 操作人，默认为"MCP系统"

    返回:
        包含操作结果的字典
    """
    try:
        # 调用后端 API 执行入库
        result = api_post("/materials/stock-in", {
            "product_name": product_name,
            "quantity": quantity,
            "reason": reason,
            "operator": operator
        })

        if result.get("success"):
            logger.info(f"入库操作: {product_name}, 数量: {quantity}, 操作人: {operator}")
        else:
            logger.warning(f"入库失败: {result.get('error')}")

        return result

    except Exception as e:
        logger.error(f"入库失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "message": f"入库失败: {str(e)}"
        }


@mcp.tool()
def stock_out(product_name: str, quantity: int, reason: str = "销售出库", operator: str = "MCP系统") -> dict:
    """
    watcher-xiaozhi 产品出库操作

    参数:
        product_name: 产品名称（必填）
        quantity: 出库数量（必填，必须大于0）
        reason: 出库原因，默认为"销售出库"
        operator: 操作人，默认为"MCP系统"

    返回:
        包含操作结果的字典
    """
    try:
        # 调用后端 API 执行出库
        result = api_post("/materials/stock-out", {
            "product_name": product_name,
            "quantity": quantity,
            "reason": reason,
            "operator": operator
        })

        if result.get("success"):
            product = result.get("product", {})
            logger.info(f"出库操作: {product_name}, 数量: {quantity}, 操作人: {operator}, 剩余: {product.get('new_quantity')}")
        else:
            logger.warning(f"出库失败: {result.get('error')}")

        return result

    except Exception as e:
        logger.error(f"出库失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "message": f"出库失败: {str(e)}"
        }


@mcp.tool()
def list_xiaozhi_products() -> dict:
    """
    列出所有 watcher-xiaozhi 相关产品

    返回:
        包含所有产品列表的字典
    """
    try:
        # 调用后端 API 获取产品列表
        data = api_get("/materials/xiaozhi")

        # 检查是否有错误
        if isinstance(data, dict) and "error" in data:
            return data

        # 处理返回的数组数据
        if isinstance(data, list):
            result = {
                "success": True,
                "count": len(data),
                "products": data,
                "message": f"查询成功，共找到 {len(data)} 种 watcher-xiaozhi 相关产品"
            }

            logger.info(f"列出所有产品，共 {len(data)} 种")
            return result
        else:
            return {
                "success": False,
                "error": "API 返回格式错误",
                "message": "无法解析产品列表"
            }

    except Exception as e:
        logger.error(f"查询产品列表失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "message": f"查询失败: {str(e)}"
        }


@mcp.tool()
def get_today_statistics() -> dict:
    """
    查询当天的仓库统计数据

    返回:
        包含今日入库数量、出库数量和当前库存总量的字典
    """
    try:
        from datetime import datetime

        # 调用后端 API 获取仪表盘统计数据
        data = api_get("/dashboard/stats")

        # 检查是否有错误
        if isinstance(data, dict) and "error" in data:
            return {
                "success": False,
                "error": data["error"],
                "message": f"查询统计数据失败: {data['error']}"
            }

        # 获取今天的日期
        today = datetime.now().strftime('%Y-%m-%d')

        result = {
            "success": True,
            "date": today,
            "statistics": {
                "today_in": data.get("today_in", 0),
                "today_out": data.get("today_out", 0),
                "total_stock": data.get("total_stock", 0),
                "low_stock_count": data.get("low_stock_count", 0),
                "net_change": data.get("today_in", 0) - data.get("today_out", 0)
            },
            "message": f"查询成功：{today} 入库 {data.get('today_in', 0)} 件，出库 {data.get('today_out', 0)} 件，当前库存总量 {data.get('total_stock', 0)} 件"
        }

        logger.info(f"查询今日统计: 入库 {data.get('today_in', 0)}, 出库 {data.get('today_out', 0)}, 库存 {data.get('total_stock', 0)}")
        return result

    except Exception as e:
        logger.error(f"查询统计数据失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "message": f"查询统计数据失败: {str(e)}"
        }


# 启动服务器
if __name__ == "__main__":
    mcp.run(transport="stdio")
