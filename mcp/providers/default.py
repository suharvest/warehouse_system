"""DefaultProvider — 对接自有仓库管理系统后端（端口 2124）

从原 warehouse_mcp.py 提取的全部业务逻辑，包括：
- 模糊匹配回退（query_stock）
- 候选项提取（stock_in / stock_out）
- 响应归一化（search）
- 净变化计算（get_today_statistics）
"""

import logging
import re
from datetime import datetime

from .base import BaseProvider

logger = logging.getLogger("WarehouseMCP")


def _normalize_batch_no(batch_no: str) -> str:
    """归一化批次号：去掉连字符/空格，补回标准格式 YYYYMMDD-NNN。

    语音输入 "20260513023" → "20260513-023"
    已标准格式 "20260513-023" → 不变
    非日期格式批次号（如 "B-2026-003"）→ 原样返回
    """
    if not batch_no:
        return batch_no
    s = re.sub(r'[\s\-－–—]+', '', batch_no).strip()
    # YYYYMMDDNNN 形式（11位纯数字）→ 在第8位后插入 -
    m = re.fullmatch(r'(\d{8})(\d+)', s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return batch_no


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

    def query_stock(self, product_name, show_batches=False):
        # 先尝试精确查询，同时做多义检测
        data = self.http_get("/materials/product-stats", params={"name": product_name})

        # 精确查询成功时，仍需检查是否存在同名多条（精确匹配只返回第一条）
        if "error" not in data:
            resolve_result = self.http_get(
                "/fuzzy-match", params={"q": product_name, "entity_type": "material"}
            )
            candidates = resolve_result.get("candidates", [])
            # 同名多条：所有候选都与查询词完全一致 → 需要用户澄清
            exact_same = [c for c in candidates if c.get("name") == product_name]
            if not resolve_result.get("confident") and len(exact_same) > 1:
                def _fmt(c):
                    sku = c.get("extra", {}).get("sku", "")
                    return f"{c['name']}（SKU: {sku}）" if sku else c["name"]
                items = "、".join(_fmt(c) for c in exact_same[:6])
                return {
                    "success": False,
                    "error": f"找到 {len(exact_same)} 个同名产品",
                    "candidates": exact_same[:6],
                    "message": (
                        f"'{product_name}' 在系统中有 {len(exact_same)} 个同名产品：{items}。"
                        "请告知需要查询哪个（可指定 SKU 或其他特征）"
                    ),
                }

        # 精确查询失败时，自动走模糊匹配
        if "error" in data:
            resolve_result = self.http_get(
                "/fuzzy-match", params={"q": product_name, "entity_type": "material"}
            )

            if resolve_result.get("confident") and resolve_result.get("best_match"):
                best = resolve_result["best_match"]
                extra = best.get("extra", {})
                resolved_variant = extra.get("variant")
                # name+variant 组合匹配时，用原始物料名查询
                if resolved_variant:
                    resolved_name = best["name"].replace(f" {resolved_variant}", "").strip()
                else:
                    resolved_name = best["name"]
                data = self.http_get("/materials/product-stats", params={"name": resolved_name})
                if "error" in data:
                    return {
                        "success": False,
                        "error": data["error"],
                        "message": f"产品 '{resolved_name}' 查询失败",
                    }
                # 标记名称经过了模糊解析
                data["resolved_from"] = product_name
                if resolved_variant:
                    data["resolved_variant"] = resolved_variant
            else:
                # 模糊匹配也无法确定，返回候选列表（带 SKU 方便区分同名项）
                candidates = resolve_result.get("candidates", [])
                if candidates:
                    def _fmt_candidate(c):
                        sku = c.get("extra", {}).get("sku", "")
                        score = c["score"]
                        return f"{c['name']}（SKU: {sku}，{score}分）" if sku else f"{c['name']}（{score}分）"
                    ranked = [_fmt_candidate(c) for c in candidates[:5]]
                    return {
                        "success": False,
                        "error": f"名称 '{product_name}' 不够明确",
                        "candidates": candidates[:5],
                        "message": f"找到以下候选产品：{', '.join(ranked)}。请告知是哪个（可指定 SKU）",
                    }
                return {
                    "success": False,
                    "error": f"未找到与 '{product_name}' 匹配的产品",
                    "message": f"系统中没有与 '{product_name}' 相似的产品",
                }

        unit = data["unit"]
        quantity = data["current_stock"]
        resolved_variant = data.pop("resolved_variant", None)

        # 始终获取批次明细（用于多位置/多变体展示）
        batches_data = self.http_get("/materials/batches", params={"name": data["name"]})
        batches_list = []
        if isinstance(batches_data, dict) and "error" not in batches_data:
            batches_list = batches_data.get("batches", [])

        # 按 variant 过滤批次并重算库存
        if resolved_variant and batches_list:
            batches_list = [b for b in batches_list if b.get("variant") == resolved_variant]
            quantity = sum(b["quantity"] for b in batches_list)

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

        status_info = f"，状态：{status}" if status else ""
        display_name = f"{data['name']} [{resolved_variant}]" if resolved_variant else data['name']

        # 多批次时自动展示每批明细（位置、变体），单批次只显示位置
        if len(batches_list) > 1:
            msg = f"查询成功：{display_name} 当前库存 {quantity} {unit}{status_info}"
            details = []
            for b in batches_list:
                label = b["batch_no"]
                if b.get("variant"):
                    label += f" [{b['variant']}]"
                details.append(f"{label}: {b['quantity']}{unit} @ {b['location'] or '未指定'}")
            msg += f"\n批次明细：\n" + "\n".join(f"  - {d}" for d in details)
        elif len(batches_list) == 1:
            loc = batches_list[0].get("location", "")
            v = batches_list[0].get("variant", "")
            loc_info = f"，位置：{loc}" if loc else ""
            var_info = f"，变体：{v}" if v else ""
            msg = f"查询成功：{display_name} 当前库存 {quantity} {unit}{status_info}{var_info}{loc_info}"
        else:
            location = data.get("location", "")
            loc_info = f"，位置：{location}" if location else ""
            msg = f"查询成功：{display_name} 当前库存 {quantity} {unit}{status_info}{loc_info}"

        product_data = {**data}
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

        if show_batches:
            result["batches"] = batches_list

        return result

    def stock_in(self, product_name, quantity, reason_category, reason_note,
                 operator, fuzzy, location=None, contact_id=None, variant=None):
        payload = {
            "product_name": product_name,
            "quantity": quantity,
            "reason_category": reason_category,
            "reason_note": reason_note or None,
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

    def stock_out(self, product_name, quantity, reason_category, reason_note,
                  operator, fuzzy, variant=None, location=None,
                  batch_no=None, location_fuzzy=False):
        payload = {
            "product_name": product_name,
            "quantity": quantity,
            "reason_category": reason_category,
            "reason_note": reason_note or None,
            "operator": operator,
            "fuzzy": fuzzy,
        }
        if variant is not None:
            payload["variant"] = variant
        if location is not None:
            payload["location"] = location
        if batch_no is not None:
            payload["batch_no"] = _normalize_batch_no(batch_no)
        if location_fuzzy:
            payload["location_fuzzy"] = True
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
        if include_batches:
            params["include_batches"] = True

        data = self.http_get("/search", params=params)

        if isinstance(data, dict) and "error" in data:
            return {"success": False, "error": data["error"], "message": f"搜索失败: {data['error']}"}

        items = data.get("items", [])

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
