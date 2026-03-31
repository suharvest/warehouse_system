"""DefaultProvider — 对接自有仓库管理系统后端（端口 2124）

从原 warehouse_mcp.py 提取的全部业务逻辑，包括：
- 模糊匹配回退（query_stock）
- 候选项提取（stock_in / stock_out）
- 响应归一化（search）
- 净变化计算（get_today_statistics）
"""

import logging
from datetime import datetime

from .base import BaseProvider

logger = logging.getLogger("WarehouseMCP")


class DefaultProvider(BaseProvider):
    """自有后端 Provider。"""

    PROVIDER_NAME = "default"

    def __init__(self, config: dict):
        # 兼容旧配置格式：顶层 api_key 字段
        if "auth" not in config and config.get("api_key"):
            config["auth"] = {
                "type": "api_key",
                "key": config["api_key"],
            }
        super().__init__(config)
        self.max_results = int(config.get("max_results", 30))

    def resolve_name(self, text, entity_type="all"):
        return self.http_get("/fuzzy-match", params={"q": text, "entity_type": entity_type})

    def query_stock(self, product_name, show_batches=False, variant=None):
        # 先尝试精确查询
        data = self.http_get("/materials/product-stats", params={"name": product_name})

        # 精确查询失败时，自动走模糊匹配
        if "error" in data:
            resolve_result = self.http_get(
                "/fuzzy-match", params={"q": product_name, "entity_type": "material"}
            )

            if resolve_result.get("confident") and resolve_result.get("best_match"):
                resolved_name = resolve_result["best_match"]["name"]
                data = self.http_get("/materials/product-stats", params={"name": resolved_name})
                if "error" in data:
                    return {
                        "success": False,
                        "error": data["error"],
                        "message": f"产品 '{resolved_name}' 查询失败",
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
                        "message": f"找到多个候选：{', '.join(names)}，请指定更精确的名称",
                    }
                return {
                    "success": False,
                    "error": f"未找到与 '{product_name}' 匹配的产品",
                    "message": f"系统中没有与 '{product_name}' 相似的产品",
                }

        unit = data["unit"]

        # 始终获取批次明细（用于变体筛选和多位置展示）
        batches_data = self.http_get("/materials/batches", params={"name": data["name"]})
        batches_list = []
        if isinstance(batches_data, dict) and "error" not in batches_data:
            batches_list = batches_data.get("batches", [])

        # 变体筛选
        if variant and batches_list:
            variant_lower = variant.replace(" ", "").lower()
            batches_list = [
                b for b in batches_list
                if b.get("variant") and b["variant"].replace(" ", "").lower() == variant_lower
            ]

        # 计算库存（变体筛选后的，或总库存）
        if variant:
            quantity = sum(b["quantity"] for b in batches_list)
        else:
            quantity = data["current_stock"]

        safe_stock = data.get("safe_stock")
        if safe_stock is not None:
            if quantity >= safe_stock:
                status = "正常"
            elif quantity >= safe_stock * 0.5:
                status = "偏低"
            else:
                status = "告急"
        else:
            status = None

        # 构建消息
        name_display = data["name"]
        if variant:
            name_display += f" [{variant}]"

        status_info = f"，状态：{status}" if status else ""

        # 多批次时展示每批明细（位置、变体），单批次时只显示位置
        if len(batches_list) > 1:
            msg = f"查询成功：{name_display} 当前库存 {quantity} {unit}{status_info}"
            details = []
            for b in batches_list:
                label = b["batch_no"]
                if b.get("variant"):
                    label += f" [{b['variant']}]"
                details.append(f"{label}: {b['quantity']}{unit} @ {b['location'] or '未指定'}")
            msg += f"\n批次明细：\n" + "\n".join(f"  - {d}" for d in details)
        elif len(batches_list) == 1:
            loc = batches_list[0].get("location", "")
            loc_info = f"，位置：{loc}" if loc else ""
            msg = f"查询成功：{name_display} 当前库存 {quantity} {unit}{status_info}{loc_info}"
        else:
            location = data.get("location", "")
            loc_info = f"，位置：{location}" if location else ""
            msg = f"查询成功：{name_display} 当前库存 {quantity} {unit}{status_info}{loc_info}"

        product_data = {**data}
        if variant:
            product_data["variant"] = variant
            product_data["current_stock"] = quantity
        if status:
            product_data["status"] = status
        else:
            product_data.pop("safe_stock", None)
            product_data.pop("status", None)

        result = {
            "success": True,
            "product": product_data,
            "message": msg,
        }

        if show_batches or variant:
            result["batches"] = batches_list

        return result

    def stock_in(self, product_name, quantity, reason, operator, fuzzy,
                 location=None, contact_id=None, variant=None):
        payload = {
            "product_name": product_name,
            "quantity": quantity,
            "reason": reason,
            "operator": operator,
            "fuzzy": fuzzy,
        }
        if location is not None:
            payload["location"] = location
        if contact_id is not None:
            payload["contact_id"] = contact_id
        if variant is not None:
            payload["variant"] = variant

        return self.http_post("/materials/stock-in", payload)

    def stock_out(self, product_name, quantity, reason, operator, fuzzy,
                  variant=None):
        payload = {
            "product_name": product_name,
            "quantity": quantity,
            "reason": reason,
            "operator": operator,
            "fuzzy": fuzzy,
        }
        if variant is not None:
            payload["variant"] = variant
        return self.http_post("/materials/stock-out", payload)

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        limit = max_results if max_results > 0 else self.max_results
        params = {"entity_type": entity_type, "page": 1, "page_size": limit, "fuzzy": fuzzy}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if status:
            params["status"] = status
        if contact_type:
            params["contact_type"] = contact_type

        data = self.http_get("/search", params=params)

        if isinstance(data, dict) and "error" in data:
            return {"success": False, "error": data["error"], "message": f"搜索失败: {data['error']}"}

        items = data.get("items", [])

        if include_batches and entity_type == "material":
            for item in items:
                item_name = item.get("name")
                if item_name:
                    batches_data = self.http_get("/materials/batches", params={"name": item_name})
                    if isinstance(batches_data, dict) and "error" not in batches_data:
                        item["batches"] = batches_data.get("batches", batches_data)
                    else:
                        item["batches"] = []

        type_label = {"material": "物料", "contact": "联系方", "operator": "操作员"}.get(
            entity_type, entity_type
        )
        total = data.get("total", 0)
        msg = f"搜索{type_label}成功，找到 {total} 条匹配记录"
        if total > len(items):
            msg += f"（已返回前 {len(items)} 条，可通过 max_results 参数调整上限）"
        return {
            "success": True,
            "count": len(items),
            "total": total,
            "items": items,
            "message": msg,
        }

    def get_today_statistics(self):
        try:
            data = self.http_get("/dashboard/stats")

            if isinstance(data, dict) and "error" in data:
                return {
                    "success": False,
                    "error": data["error"],
                    "message": f"查询统计数据失败: {data['error']}",
                }

            today = datetime.now().strftime("%Y-%m-%d")

            return {
                "success": True,
                "date": today,
                "statistics": {
                    "today_in": data.get("today_in", 0),
                    "today_out": data.get("today_out", 0),
                    "total_stock": data.get("total_stock", 0),
                    "low_stock_count": data.get("low_stock_count", 0),
                    "net_change": data.get("today_in", 0) - data.get("today_out", 0),
                },
                "message": (
                    f"查询成功：{today} 入库 {data.get('today_in', 0)} 件，"
                    f"出库 {data.get('today_out', 0)} 件，"
                    f"当前库存总量 {data.get('total_stock', 0)} 件"
                ),
            }

        except Exception as e:
            logger.error(f"查询统计数据失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"查询统计数据失败: {e}",
            }
