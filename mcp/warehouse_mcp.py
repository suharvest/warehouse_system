#!/usr/bin/env python3
"""
仓库管理系统 MCP 服务器

提供通用的库存管理功能：
- 名称模糊解析（resolve_name）
- 库存查询（query_stock，内建模糊匹配）
- 入库/出库操作（stock_in / stock_out，内建模糊匹配）
- 统一搜索（search，支持物料/联系方/操作员）
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
        'api_key': '',
        'max_results': 30,  # MCP 搜索结果上限
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
MAX_RESULTS = int(_config.get('max_results', 30))

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
def resolve_name(text: str, entity_type: str = "all") -> dict:
    """
    将模糊文本（语音识别、用户口语输入等）解析为系统中精确的实体名称。

    适用场景：
    - 用户说了一个不确定的名称，需要先确认再操作
    - 需要同时搜索物料、联系方、操作员（entity_type="all"）
    - 需要获取候选列表让用户选择

    注意：query_stock、stock_in、stock_out 已内建模糊匹配，
    大多数情况下可直接调用，无需先调 resolve_name。
    仅在需要「先确认名称再决定下一步」时才需要本工具。

    参数:
        text: 需要解析的文本（如语音识别结果"螺丝钉"、"张三"等）
        entity_type: 实体类型，"material" | "contact" | "operator" | "all"（默认搜索全部类型）

    返回:
        best_match: 最佳匹配（含 name, score, entity_type）
        confident: 是否高置信度（true 时可直接使用 best_match）
        candidates: 候选列表（confident=false 时需从中选择）
    """
    return api_get("/fuzzy-match", params={"q": text, "entity_type": entity_type})


@mcp.tool()
def query_stock(product_name: str, show_batches: bool = False) -> dict:
    """
    查询产品库存详情。支持模糊名称输入，内建自动解析。

    可直接传入不精确的名称（如语音识别结果），工具会自动模糊匹配。
    无需先调用 resolve_name。

    参数:
        product_name: 产品名称（支持模糊输入，如"螺丝"、"luo si"等）
        show_batches: 是否同时返回批次明细（默认 false）。
                      当用户询问「在哪里」「什么位置」「库位」「哪个货架」等位置相关问题时，
                      建议设为 true，因为不同批次可能存放在不同位置。

    返回:
        success=true 时：产品库存详情（name, sku, current_stock, unit, safe_stock,
        location, today_in, today_out, status 等）。若 show_batches=true，额外包含
        batches 列表（每个批次含 batch_no, quantity, location, contact_name）。
        success=false 时：如有候选项会在 candidates 中列出
    """
    # 先尝试精确查询
    data = api_get("/materials/product-stats", params={"name": product_name})

    # 精确查询失败时，自动走模糊匹配
    if "error" in data:
        resolve_result = api_get("/fuzzy-match", params={"q": product_name, "entity_type": "material"})

        if resolve_result.get("confident") and resolve_result.get("best_match"):
            resolved_name = resolve_result["best_match"]["name"]
            data = api_get("/materials/product-stats", params={"name": resolved_name})
            if "error" in data:
                return {
                    "success": False,
                    "error": data["error"],
                    "message": f"产品 '{resolved_name}' 查询失败"
                }
            # 标记名称经过了模糊解析
            data["resolved_from"] = product_name
        else:
            # 模糊匹配也无法确定，返回候选列表
            candidates = resolve_result.get("candidates", [])
            if candidates:
                names = [c["name"] for c in candidates[:5]]
                return {
                    "success": False,
                    "error": f"名称 '{product_name}' 不够明确",
                    "candidates": candidates[:5],
                    "message": f"找到多个候选：{', '.join(names)}，请指定更精确的名称"
                }
            return {
                "success": False,
                "error": f"未找到与 '{product_name}' 匹配的产品",
                "message": f"系统中没有与 '{product_name}' 相似的产品"
            }

    quantity = data["current_stock"]
    safe_stock = data["safe_stock"]
    if quantity >= safe_stock:
        status = "正常"
    elif quantity >= safe_stock * 0.5:
        status = "偏低"
    else:
        status = "告急"

    location = data.get("location", "")
    loc_info = f"，位置：{location}" if location else ""
    msg = f"查询成功：{data['name']} 当前库存 {quantity} {data['unit']}，状态：{status}{loc_info}"

    result = {
        "success": True,
        "product": {**data, "status": status},
        "message": msg
    }

    if show_batches:
        batches_data = api_get("/materials/batches", params={"name": data["name"]})
        if isinstance(batches_data, dict) and "error" not in batches_data:
            batches_list = batches_data.get("batches", [])
            result["batches"] = batches_list
            if batches_list:
                details = [f"{b['batch_no']}: {b['quantity']}{data['unit']} @ {b['location'] or '未指定'}"
                           for b in batches_list]
                result["message"] += f"\n批次明细：\n" + "\n".join(f"  - {d}" for d in details)
        else:
            result["batches"] = []

    return result


@mcp.tool()
def stock_in(product_name: str, quantity: int, reason: str = "采购入库",
             operator: str = "MCP系统", fuzzy: bool = True,
             location: str = None, contact_id: int = None) -> dict:
    """
    产品入库。可直接传入模糊名称，自动解析为精确产品。

    参数:
        product_name: 产品名称（支持模糊输入，如"螺丝"会自动匹配"M3螺丝"）
        quantity: 入库数量（正整数）
        reason: 入库原因（默认"采购入库"）
        operator: 操作人（默认"MCP系统"）
        fuzzy: 是否启用模糊匹配（默认 true）
        location: 存放位置（可选，如"A区-01架"）
        contact_id: 关联联系方 ID（可选，如供应商 ID）

    返回:
        success=true 时：入库成功，含批次信息和产品详情
        success=false 且有 candidates 时：名称不够明确，需用候选中的精确名称重试
    """
    payload = {
        "product_name": product_name,
        "quantity": quantity,
        "reason": reason,
        "operator": operator,
        "fuzzy": fuzzy
    }
    if location is not None:
        payload["location"] = location
    if contact_id is not None:
        payload["contact_id"] = contact_id

    result = api_post("/materials/stock-in", payload)

    if not result.get("success") and "candidates" in result.get("detail", {}):
        candidates = result["detail"]["candidates"]
        names = [c["name"] for c in candidates[:5]]
        result["message"] = f"名称 '{product_name}' 不够明确，候选：{', '.join(names)}。请用精确名称重试。"

    return result


@mcp.tool()
def stock_out(product_name: str, quantity: int, reason: str = "销售出库",
              operator: str = "MCP系统", fuzzy: bool = True) -> dict:
    """
    产品出库。可直接传入模糊名称，自动解析为精确产品。按 FIFO 消耗批次。

    参数:
        product_name: 产品名称（支持模糊输入，如"螺丝"会自动匹配"M3螺丝"）
        quantity: 出库数量（正整数）
        reason: 出库原因（默认"销售出库"）
        operator: 操作人（默认"MCP系统"）
        fuzzy: 是否启用模糊匹配（默认 true）

    返回:
        success=true 时：出库成功，含批次消耗详情
        success=false 且有 candidates 时：名称不够明确，需用候选中的精确名称重试
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
def search(query: str = None, entity_type: str = "material",
           category: str = None, status: str = None,
           contact_type: str = None, fuzzy: bool = True,
           include_batches: bool = False,
           max_results: int = 0) -> dict:
    """
    统一搜索工具，可搜索物料、联系方、操作员。支持模糊匹配。

    常见用法：
    - 搜物料："帮我找螺丝相关的产品" → search(query="螺丝", entity_type="material")
    - 查库存告急："哪些产品库存不足" → search(status="danger,warning", entity_type="material")
    - 找供应商："搜索张三" → search(query="张三", entity_type="contact", contact_type="supplier")
    - 找操作员："小李是谁" → search(query="小李", entity_type="operator")

    参数:
        query: 搜索关键词（支持模糊输入，如语音识别结果）
        entity_type: 搜索类型 "material"(物料) | "contact"(联系方) | "operator"(操作员)
        category: 物料分类过滤（仅 material 有效，精确匹配）
        status: 库存状态过滤（仅 material 有效），可选值：
                "normal"(正常) / "warning"(偏低) / "danger"(告急)，多个用逗号分隔
        contact_type: 联系方类型过滤（仅 contact 有效），"supplier"(供应商) / "customer"(客户)
        fuzzy: 是否启用模糊匹配（默认 true）
        include_batches: 搜索物料时是否附带每个物料的批次列表（默认 false，仅 entity_type="material" 有效）
        max_results: 返回结果上限（0 表示使用配置默认值，当前默认 {MAX_RESULTS}）

    返回:
        items: 匹配结果列表（include_batches=true 时每个物料含 batches 字段）
        total: 总匹配数（可能大于返回的 items 数量）
    """
    limit = max_results if max_results > 0 else MAX_RESULTS
    params = {"entity_type": entity_type, "page": 1, "page_size": limit, "fuzzy": fuzzy}
    if query:
        params["q"] = query
    if category:
        params["category"] = category
    if status:
        params["status"] = status
    if contact_type:
        params["contact_type"] = contact_type

    data = api_get("/search", params=params)

    if isinstance(data, dict) and "error" in data:
        return {"success": False, "error": data["error"], "message": f"搜索失败: {data['error']}"}

    items = data.get("items", [])

    if include_batches and entity_type == "material":
        for item in items:
            item_name = item.get("name")
            if item_name:
                batches_data = api_get("/materials/batches", params={"name": item_name})
                if isinstance(batches_data, dict) and "error" not in batches_data:
                    item["batches"] = batches_data.get("batches", batches_data)
                else:
                    item["batches"] = []

    type_label = {"material": "物料", "contact": "联系方", "operator": "操作员"}.get(entity_type, entity_type)
    total = data.get("total", 0)
    msg = f"搜索{type_label}成功，找到 {total} 条匹配记录"
    if total > len(items):
        msg += f"（已返回前 {len(items)} 条，可通过 max_results 参数调整上限）"
    return {
        "success": True,
        "count": len(items),
        "total": total,
        "items": items,
        "message": msg
    }


@mcp.tool()
def get_today_statistics() -> dict:
    """
    查询当天仓库统计概览。无需参数，直接调用即可。

    返回：今日入库量、出库量、库存总量、低库存数量、净变化量。
    适用于：「今天仓库情况怎么样」「今日出入库汇总」等问题。
    """
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
