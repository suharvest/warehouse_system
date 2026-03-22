#!/usr/bin/env python3
"""
仓库管理系统 MCP 服务器

提供通用的库存管理功能：
- 名称模糊解析（resolve_name）
- 库存查询（query_stock）
- 入库/出库操作（stock_in / stock_out）
- 物料搜索（search_materials）
- 当天统计（get_today_statistics）

注意：本服务通过调用后端 API 实现所有操作，不直接操作数据库。
确保后端服务（端口 2124）已启动后再使用 MCP 服务。
"""

from fastmcp import FastMCP
import sys
import os
import logging
import requests
import yaml

# 配置日志
logger = logging.getLogger('WarehouseMCP')

# 修复 Windows 控制台 UTF-8 编码
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

# 加载配置文件
def load_config():
    """从 config.yml 加载配置，支持环境变量覆盖"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.yml')
    config = {
        'api_base_url': 'http://localhost:2124/api',
        'api_key': ''
    }

    # 尝试读取配置文件
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f) or {}
                config.update(file_config)
        except Exception as e:
            logger.warning(f"读取配置文件失败: {e}，使用默认配置")

    # 环境变量优先级更高
    if os.environ.get("WAREHOUSE_API_URL"):
        config['api_base_url'] = os.environ.get("WAREHOUSE_API_URL")
    if os.environ.get("WAREHOUSE_API_KEY"):
        config['api_key'] = os.environ.get("WAREHOUSE_API_KEY")

    return config

_config = load_config()
API_BASE_URL = _config['api_base_url']
API_KEY = _config['api_key']

# 创建 MCP 服务器
mcp = FastMCP("Warehouse System")


def api_get(endpoint: str, params: dict = None) -> dict:
    """发送 GET 请求到后端 API"""
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        response = requests.get(f"{API_BASE_URL}{endpoint}", params=params, headers=headers, timeout=10)
        data = response.json()
        if response.status_code >= 400:
            return {"success": False, "error": data.get("detail", str(data)), "message": f"API 返回错误 ({response.status_code})"}
        return data
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
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        response = requests.post(f"{API_BASE_URL}{endpoint}", json=data, headers=headers, timeout=10)
        data = response.json()
        if response.status_code >= 400:
            return {"success": False, "error": data.get("detail", str(data)), "detail": data.get("detail"), "message": f"API 返回错误 ({response.status_code})"}
        return data
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
def resolve_name(text: str, entity_type: str = "material") -> dict:
    """
    将语音识别或模糊文本解析为精确的实体名称。

    在对产品名称不确定时使用此工具。返回候选列表和置信度分数。
    当 confident=true 时，best_match 可以直接使用。
    当 confident=false 时，需要从 candidates 中选择最合适的。

    参数:
        text: 需要解析的文本（如 ASR 识别结果）
        entity_type: 实体类型，"material"(物料) | "contact"(联系方) | "operator"(操作员) | "all"(全部)

    返回:
        包含 best_match, confident, candidates 的字典
    """
    return api_get("/fuzzy-match", params={"q": text, "entity_type": entity_type})


@mcp.tool()
def query_stock(product_name: str) -> dict:
    """
    查询指定产品的库存详情，包括当前库存、安全库存、今日出入库等。

    参数:
        product_name: 产品名称（精确名称，如不确定请先用 resolve_name 解析）

    返回:
        产品库存详情，包含 name, sku, current_stock, unit, safe_stock, location,
        today_in, today_out, total_in, total_out 等字段
    """
    data = api_get("/materials/product-stats", params={"name": product_name})
    if "error" in data:
        return {
            "success": False,
            "error": data["error"],
            "message": f"产品 '{product_name}' 不存在，请用 resolve_name 工具确认名称"
        }

    quantity = data["current_stock"]
    safe_stock = data["safe_stock"]
    if quantity >= safe_stock:
        status = "正常"
    elif quantity >= safe_stock * 0.5:
        status = "偏低"
    else:
        status = "告急"

    return {
        "success": True,
        "product": {**data, "status": status},
        "message": f"查询成功：{data['name']} 当前库存 {quantity} {data['unit']}，状态：{status}"
    }


@mcp.tool()
def stock_in(product_name: str, quantity: int, reason: str = "采购入库",
             operator: str = "MCP系统", fuzzy: bool = True) -> dict:
    """
    产品入库操作。默认启用模糊匹配，自动解析不精确的产品名称。

    参数:
        product_name: 产品名称（支持模糊输入，如语音识别结果）
        quantity: 入库数量（必须大于0）
        reason: 入库原因
        operator: 操作人
        fuzzy: 是否启用模糊名称解析（默认开启）

    返回:
        操作结果，包含产品信息和批次详情。如名称被模糊解析，会包含 resolved_from 字段。
    """
    result = api_post("/materials/stock-in", {
        "product_name": product_name,
        "quantity": quantity,
        "reason": reason,
        "operator": operator,
        "fuzzy": fuzzy
    })

    if not result.get("success") and "candidates" in result.get("detail", {}):
        candidates = result["detail"]["candidates"]
        names = [c["name"] for c in candidates[:5]]
        result["message"] = f"名称 '{product_name}' 不够明确，候选：{', '.join(names)}。请用精确名称重试。"

    return result


@mcp.tool()
def stock_out(product_name: str, quantity: int, reason: str = "销售出库",
              operator: str = "MCP系统", fuzzy: bool = True) -> dict:
    """
    产品出库操作。默认启用模糊匹配。

    参数:
        product_name: 产品名称（支持模糊输入）
        quantity: 出库数量（必须大于0）
        reason: 出库原因
        operator: 操作人
        fuzzy: 是否启用模糊名称解析（默认开启）

    返回:
        操作结果，包含批次消耗详情（FIFO）。
    """
    result = api_post("/materials/stock-out", {
        "product_name": product_name,
        "quantity": quantity,
        "reason": reason,
        "operator": operator,
        "fuzzy": fuzzy
    })

    if not result.get("success") and "candidates" in result.get("detail", {}):
        candidates = result["detail"]["candidates"]
        names = [c["name"] for c in candidates[:5]]
        result["message"] = f"名称 '{product_name}' 不够明确，候选：{', '.join(names)}。请用精确名称重试。"

    return result


@mcp.tool()
def search_materials(query: str = None, category: str = None,
                     status: str = None, fuzzy: bool = True) -> dict:
    """
    搜索库存物料，支持多条件组合和模糊匹配。

    参数:
        query: 物料名称或SKU关键词（支持模糊搜索）
        category: 物料分类（精确匹配）
        status: 库存状态 (normal=正常, warning=偏低, danger=告急)，多个用逗号分隔
        fuzzy: 是否启用模糊匹配（默认开启）

    返回:
        符合条件的物料列表
    """
    params = {"page": 1, "page_size": 100, "fuzzy": str(fuzzy).lower()}
    if query:
        params["q"] = query
    if category:
        params["category"] = category
    if status:
        params["status"] = status

    data = api_get("/search", params=params)

    if isinstance(data, dict) and "error" in data:
        return {"success": False, "error": data["error"], "message": f"搜索失败: {data['error']}"}

    items = data.get("items", [])
    return {
        "success": True,
        "count": len(items),
        "total": data.get("total", 0),
        "materials": items,
        "message": f"搜索成功，找到 {data.get('total', 0)} 条匹配记录"
    }


@mcp.tool()
def get_today_statistics() -> dict:
    """查询当天的仓库统计数据，包括入库数量、出库数量、库存总量。"""
    try:
        from datetime import datetime

        data = api_get("/dashboard/stats")

        if isinstance(data, dict) and "error" in data:
            return {
                "success": False,
                "error": data["error"],
                "message": f"查询统计数据失败: {data['error']}"
            }

        today = datetime.now().strftime('%Y-%m-%d')

        return {
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
