"""DefaultProvider — 对接自有仓库管理系统后端（端口 2124）

从原 warehouse_mcp.py 提取的全部业务逻辑，包括：
- 模糊匹配回退（query_stock）
- 候选项提取（stock_in / stock_out）
- 响应归一化（search）
- 净变化计算（get_today_statistics）
"""

import json
import logging
import re
from datetime import datetime

from .base import BaseProvider

# Cloud WS gateway (watcher-agent-api.seeed.cc) caps inbound frames around 13 KB.
# Trim per-tool responses to a safer threshold so a single big result can't kill
# the WebSocket with 1009. Critical for `search` with include_batches=True.
_MCP_RESPONSE_BUDGET_BYTES = 10_000

logger = logging.getLogger("WarehouseMCP")

_BATCH_NO_RE = re.compile(r"^\d{8}-\d+$")

# reason_category 归一化映射：覆盖 LLM 口语 / 中文别名 / 常见英文同义词。
# 目标是把 4B 模型胡乱传的"production / use / scrap / 领用"等映射回后端枚举。
# 后端权威枚举（database.py:21）：
#   in:  purchase return refund produce transfer_in other_in
#   out: sell lend consume loss transfer_out other_out
_REASON_ALIAS = {
    # ===== IN =====
    "purchase": "purchase", "采购": "purchase", "进货": "purchase", "采购入库": "purchase",
    "buy": "purchase", "buying": "purchase",
    "return": "return", "退还": "return", "归还": "return", "借还": "return",
    "refund": "refund", "退货": "refund", "退货入库": "refund", "退款": "refund",
    "produce": "produce", "生产": "produce", "生产入库": "produce", "production": "produce",
    "produced": "produce", "manufacture": "produce", "completed": "produce",
    "transfer_in": "transfer_in", "调入": "transfer_in", "调拨入库": "transfer_in",
    "other_in": "other_in", "其他入库": "other_in",
    # ===== OUT =====
    "sell": "sell", "sale": "sell", "sold": "sell", "出售": "sell", "销售": "sell",
    "销售出库": "sell",
    "lend": "lend", "borrow": "lend", "loan": "lend", "借出": "lend",
    "consume": "consume", "use": "consume", "used": "consume", "using": "consume",
    "usage": "consume", "consumption": "consume", "consumed": "consume",
    "领用": "consume", "消耗": "consume", "研发领用": "consume", "生产领料": "consume",
    "生产领用": "consume",
    "loss": "loss", "scrap": "loss", "scrapped": "loss", "损耗": "loss", "损失": "loss",
    "report_loss": "loss",
    "transfer_out": "transfer_out", "调出": "transfer_out", "调拨出库": "transfer_out",
    "transfer": "transfer_out",  # 不带方向的歧义 -> 按出库处理（更安全）
    "other_out": "other_out", "其他出库": "other_out", "返修出库": "other_out",
    "返修": "other_out",
}


# 跨枚举误传映射：LLM 可能把 stock_in 的枚举值传给 stock_out（反之亦然）。
# 在语义上做最佳匹配。例如 produce（入库枚举）在出库语境下 → consume（领用）。
_CROSS_ENUM_ALIAS = {
    # IN → OUT
    ("produce", "stock_out"): "consume",
    ("purchase", "stock_out"): "other_out",
    ("refund", "stock_out"): "other_out",
    # OUT → IN
    ("consume", "stock_in"): "produce",
    ("sell", "stock_in"): "other_in",
    ("lend", "stock_in"): "transfer_in",
    ("loss", "stock_in"): "other_in",
    ("report_loss", "stock_in"): "other_in",
}


def _normalize_reason_category(value, operation: str | None = None):
    """把 LLM/用户传来的 reason_category 归一化为后端合法枚举值。

    未命中映射时返回原值，由后端做最终拒绝（fail-closed）。
    operation 可选 "stock_in" / "stock_out"，用于解决跨枚举误传。"""
    if value is None:
        return value
    key = str(value).strip().lower()
    # 先查通用别名
    mapped = _REASON_ALIAS.get(key)
    if mapped is not None:
        # 检查是否跨枚举误传：当前枚举里没有映射后的值，但另一个枚举里有
        if operation and mapped == key:
            cross = _CROSS_ENUM_ALIAS.get((key, operation))
            if cross is not None:
                return cross
        return mapped
    return value


def _normalize_batch_no(batch_no: str) -> str:
    """归一化批次号，覆盖 ASR / 用户口语输入的常见噪声。

    支持的归一化：
    1. 大小写：包含字母时统一大写（"b-2026-003" → "B-2026-003"）
    2. 全角/中文连字符 / em-dash 转半角连字符
    3. 空格分隔 → 连字符（"SEED BRG NJ409" → "SEED-BRG-NJ409"）
    4. 纯数字 YYYYMMDDNNN（11+ 位）→ 第 8 位后插 -（"20260513023" → "20260513-023"）
    5. 多个连续连字符压缩
    标准格式（"B-2026-003", "SEED-BRG-NJ409"）→ 大小写归一后不变
    """
    if not batch_no:
        return batch_no
    s = batch_no.strip()
    # 全角/中文连字符 → 半角；全角空格 → 半角空格
    s = s.translate(str.maketrans({'－': '-', '–': '-', '—': '-', '　': ' '}))
    # 含字母 → 大写
    if re.search(r'[a-zA-Z]', s):
        s = s.upper()
    # 空格 → 连字符
    s = re.sub(r'\s+', '-', s)
    # 多个连续连字符压成一个
    s = re.sub(r'-+', '-', s)
    # 纯数字 YYYYMMDDNNN：去掉所有 - 后第 8 位插 -
    digits_only = s.replace('-', '')
    m = re.fullmatch(r'(\d{8})(\d+)', digits_only)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return s


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
        self.max_results = int(config.get("max_results", 10))

    def resolve_name(self, text, entity_type="all"):
        return self.http_get("/fuzzy-match", params={"q": text, "entity_type": entity_type})

    def query_stock(self, product_name, show_batches=False, _routing_fallback=False):
        def _batch_fallback(original_resp):
            if _routing_fallback or not _BATCH_NO_RE.fullmatch(str(product_name or "").strip()):
                return original_resp
            batch_resp = self.query_batch(product_name, _routing_fallback=True)
            if isinstance(batch_resp, dict) and batch_resp.get("success"):
                return batch_resp
            return original_resp

        # 先尝试精确查询，同时做多义检测
        resolved_material_id = None
        data = self.http_get("/materials/product-stats", params={"name": product_name})

        def _spec_of(c):
            """候选的规格描述：单值 variant 优先，其次 variants 列表用 / 连接。"""
            extra = c.get("extra") or {}
            return extra.get("variant") or "/".join(extra.get("variants") or [])

        def _fmt(c):
            spec = _spec_of(c)
            if spec:
                return f"{c['name']}（{spec}）"
            sku = (c.get("extra") or {}).get("sku", "")
            return f"{c['name']}（SKU: {sku}）" if sku else c["name"]

        # 精确查询成功时，仍需检查是否存在同名多条（精确匹配只返回第一条）
        if "error" not in data:
            resolve_result = self.http_get(
                "/fuzzy-match", params={"q": product_name, "entity_type": "material"}
            )
            candidates = resolve_result.get("candidates", [])
            # 同名多条：所有候选都与查询词完全一致 → 需要用户澄清
            exact_same = [c for c in candidates if c.get("name") == product_name]
            if not resolve_result.get("confident") and len(exact_same) > 1:
                items = "、".join(_fmt(c) for c in exact_same[:6])
                return {
                    "success": False,
                    "error": f"找到 {len(exact_same)} 个同名产品",
                    "candidates": exact_same[:6],
                    "message": (
                        f"'{product_name}' 在系统中有 {len(exact_same)} 个同名产品：{items}。"
                        "请告知需要查询哪个（可指定规格或 SKU）"
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
                resolved_name = extra.get("canonical_name")
                # name+variant 组合匹配时，用原始物料名查询
                if not resolved_name and resolved_variant:
                    resolved_name = best["name"].replace(f" {resolved_variant}", "").strip()
                elif not resolved_name:
                    resolved_name = best["name"]
                # entity_id 可直接精确定位物料，避免同名多行时按 name 回查盲取错料
                resolved_material_id = best.get("entity_id")
                if resolved_material_id:
                    stats_params = {"material_id": resolved_material_id}
                else:
                    stats_params = {"name": resolved_name}
                data = self.http_get("/materials/product-stats", params=stats_params)
                if "error" in data:
                    return _batch_fallback({
                        "success": False,
                        "error": data["error"],
                        "message": f"产品 '{resolved_name}' 查询失败",
                    })
                # 标记名称经过了模糊解析
                data["resolved_from"] = product_name
                if resolved_variant:
                    data["resolved_variant"] = resolved_variant
            else:
                # 模糊匹配也无法确定，返回候选列表（带 SKU 方便区分同名项）
                candidates = resolve_result.get("candidates", [])
                if candidates:
                    def _fmt_candidate(c):
                        spec = _spec_of(c)
                        score = c["score"]
                        if spec:
                            return f"{c['name']}（{spec}，{score}分）"
                        sku = c.get("extra", {}).get("sku", "")
                        return f"{c['name']}（SKU: {sku}，{score}分）" if sku else f"{c['name']}（{score}分）"
                    ranked = [_fmt_candidate(c) for c in candidates[:5]]
                    return {
                        "success": False,
                        "error": f"名称 '{product_name}' 不够明确",
                        "candidates": candidates[:5],
                        "message": f"找到以下候选产品：{', '.join(ranked)}。请告知是哪个（可指定规格或 SKU）",
                    }
                return _batch_fallback({
                    "success": False,
                    "error": f"未找到与 '{product_name}' 匹配的产品",
                    "message": f"系统中没有与 '{product_name}' 相似的产品",
                })

        unit = data["unit"]
        quantity = data["current_stock"]
        resolved_variant = data.pop("resolved_variant", None)

        # 始终获取批次明细（用于多位置/多变体展示）
        if resolved_material_id:
            batch_params = {"material_id": resolved_material_id}
        else:
            batch_params = {"name": data["name"]}
        batches_data = self.http_get("/materials/batches", params=batch_params)
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
        if resolved_variant:
            # variant 过滤后重算的库存必须写回 product，否则下游
            # _wrap_response 播报 product.current_stock 时读到全规格总量
            product_data["current_stock"] = quantity
            product_data["variant"] = resolved_variant
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
                 operator, fuzzy, location=None, contact_id=None, variant=None,
                 allow_new_variant=False, actual_operator=None):
        payload = {
            "product_name": product_name,
            "quantity": quantity,
            "reason_category": _normalize_reason_category(reason_category, "stock_in"),
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
        if allow_new_variant:
            payload["allow_new_variant"] = True
        if actual_operator:
            payload["actual_operator"] = actual_operator

        return self.http_post("/materials/stock-in", payload)

    def stock_out(self, product_name, quantity, reason_category, reason_note,
                  operator, fuzzy, variant=None, location=None,
                  batch_no=None, location_fuzzy=False,
                  allow_partial_fallback=False, actual_operator=None):
        payload = {
            "product_name": product_name,
            "quantity": quantity,
            "reason_category": _normalize_reason_category(reason_category, "stock_out"),
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
        if allow_partial_fallback:
            payload["allow_partial_fallback"] = True
        if actual_operator:
            payload["actual_operator"] = actual_operator
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

        # Size guard: items is already sorted by relevance score desc from backend,
        # so dropping from the tail keeps the most relevant matches. A fuzzy query
        # like "D015343" can match 26 materials and with include_batches=True the
        # serialized response easily exceeds 16 KB — the cloud gateway then rejects
        # with WS close 1009. Trim by relevance until under _MCP_RESPONSE_BUDGET_BYTES.
        truncated_by_size = False
        while items and len(json.dumps(items, ensure_ascii=False).encode("utf-8")) > _MCP_RESPONSE_BUDGET_BYTES:
            items.pop()  # tail = lowest score
            truncated_by_size = True

        type_label = {"material": "物料", "contact": "联系方", "operator": "操作员"}.get(
            entity_type, entity_type
        )
        total = data.get("total", 0)
        msg = f"搜索{type_label}成功，找到 {total} 条匹配记录"
        if total > len(items):
            if truncated_by_size:
                msg += (
                    f"（结果较大，已截断到前 {len(items)} 条以避免传输上限；"
                    "如需更多细节请用更具体的 query 或改用 query_stock/query_batch）"
                )
            else:
                msg += f"（已返回前 {len(items)} 条，可通过 max_results 参数调整上限）"
        result = {
            "success": True,
            "count": len(items),
            "total": total,
            "items": items,
            "message": msg,
        }
        if truncated_by_size:
            result["truncated"] = True
        return result

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

    def query_batch(self, batch_no, _routing_fallback=False):
        resp = self.http_get(
            "/batches/by-no",
            params={"batch_no": _normalize_batch_no(batch_no), "include_exhausted": True},
        )
        if _routing_fallback:
            return resp
        if isinstance(resp, dict) and resp.get("success"):
            return resp
        text = str(batch_no or "").strip()
        looks_like_product = bool(re.search(r"[\u4e00-\u9fff]", text)) or not _BATCH_NO_RE.fullmatch(
            _normalize_batch_no(text)
        )
        if looks_like_product:
            stock_resp = self.query_stock(text, _routing_fallback=True)
            if isinstance(stock_resp, dict) and stock_resp.get("success"):
                return stock_resp
        return resp

    def move_batch_location(self, batch_no, new_location, quantity=None,
                            from_location=None, product_name=None,
                            operator="MCP系统"):
        payload = {
            "batch_no": _normalize_batch_no(batch_no),
            "new_location": new_location,
            "operator": operator,
        }
        if quantity is not None:
            payload["quantity"] = quantity
        if from_location is not None:
            payload["from_location"] = from_location
        if product_name is not None:
            payload["product_name"] = product_name
        return self.http_post("/materials/batches/move-location", payload)
