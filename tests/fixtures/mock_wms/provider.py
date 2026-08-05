"""对接客户自有 WMS 系统的 Provider（完整实现 6 个核心方法）

客户 WMS 接口契约（http://<host>:3000）：
  GET  /api/inventory     -> {code, data:{items:[...], summary:{...}}, msg}
  GET  /api/products      -> {code, data:[{id,code,name,spec,unit,stock,minStock}], msg}
  GET  /api/transactions  -> {code, data:[{id,time,type,productId,code,name,qty,operator,remark}], msg}
  POST /api/inbound       <- {productCode, qty, operator, remark}
  POST /api/outbound      <- {productCode, qty, operator, remark}
  统一响应：code=0 成功，code=1 失败（msg 为原因，如 "库存不足,当前库存:8"）

注意：本文件同时要能被两条加载路径导入——
  1. providers/__init__.py 的 _discover()  → 包内相对导入可用
  2. providers/test_runner.load_provider_from_file() → 模块名无父包，相对导入失败
所以 BaseProvider 采用「绝对导入优先、相对导入兜底」的双路径写法。
"""

import difflib
import logging
import re
from datetime import datetime

try:  # ERP 动态加载路径（spec 名无父包）
    from providers.base import BaseProvider
except ImportError:  # 包内 _discover() 路径
    from ..base import BaseProvider

logger = logging.getLogger("WarehouseMCP")

# 模糊匹配判定为「有把握」的最低分，低于此分只返回候选让 LLM 追问澄清
_CONFIDENT_SCORE = 0.75
# 进入候选列表的最低分，过滤掉完全不相关的噪声
_CANDIDATE_FLOOR = 0.34


def _norm(text) -> str:
    """归一化：去空白、转小写、全角转半角，提升 ASR 文本的匹配率。"""
    if not text:
        return ""
    s = str(text).strip().lower()
    s = s.translate(str.maketrans({"－": "-", "–": "-", "—": "-", "　": " "}))
    return re.sub(r"\s+", "", s)


class CustomWmsProvider(BaseProvider):
    """客户自有 WMS 适配器。"""

    PROVIDER_NAME = "custom_wms"

    def __init__(self, config: dict):
        super().__init__(config)
        # 连接级绑定优先于 Provider 自身的静态配置：多个智能体绑不同外部仓库时，
        # 每个连接一个 Provider 实例，各自拿到自己的 external_warehouse_id。
        self.tenant_id = config.get("external_tenant_id") or config.get("tenant_id")
        # 注意**不要**凭空造一个默认仓库编码。没有绑定时（如连通性测试、或连接未选
        # 外部仓库）应当不传 warehouse，让对方系统用它自己的默认仓；传一个我方
        # 臆造的 "default" 会被对方判为"未知仓库"，L1 测试直接挂。
        self.warehouse_id = (config.get("external_warehouse_id")
                             or config.get("warehouse_id") or None)
        self.max_results = int(config.get("max_results", 10))

    def _wh_params(self, extra=None):
        """所有读写都要带上本连接绑定的仓库，否则会打到对方的默认仓。"""
        p = {"warehouse": self.warehouse_id} if self.warehouse_id else {}
        if extra:
            p.update(extra)
        return p

    # ── 内部工具 ──

    def _fetch_products(self):
        """拉取全量产品，返回 (products, error_response)。"""
        data = self.http_get("/api/products", params=self._wh_params())
        if not isinstance(data, dict) or data.get("code") != 0:
            msg = (data or {}).get("msg") or (data or {}).get("message") or "未知错误"
            return None, {
                "success": False,
                "error": "api_error",
                "message": f"访问 WMS 产品列表失败: {msg}",
            }
        items = data.get("data") or []
        return (items if isinstance(items, list) else []), None

    def _score(self, query: str, product: dict) -> float:
        """给单个产品打匹配分（0~1）。名称/编码/规格三路取最大值。"""
        q = _norm(query)
        if not q:
            return 0.0

        best = 0.0
        name = _norm(product.get("name"))
        code = _norm(product.get("code"))
        spec = _norm(product.get("spec"))

        # 编码完全一致 → 直接满分（"SP-001"）
        if code and q == code:
            return 1.0
        # 名称完全一致 → 满分
        if name and q == name:
            return 1.0
        # "矿泉水500ml" 这类「名称+规格」连读
        if name and spec and q == name + spec:
            return 1.0

        for field, weight in ((name, 1.0), (code, 0.9), (spec, 0.6)):
            if not field:
                continue
            if q in field or field in q:
                # 子串命中，按长度比例给分，避免单字"水"命中"矿泉水"拿高分
                ratio = min(len(q), len(field)) / max(len(q), len(field))
                best = max(best, weight * (0.72 + 0.28 * ratio))
            best = max(best, weight * difflib.SequenceMatcher(None, q, field).ratio())
        return best

    def _rank(self, query: str, products: list) -> list:
        """按匹配分降序返回 [(score, product), ...]，已过滤低分噪声。"""
        scored = [(self._score(query, p), p) for p in products]
        scored = [x for x in scored if x[0] >= _CANDIDATE_FLOOR]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    @staticmethod
    def _as_candidate(score: float, p: dict) -> dict:
        """转成 MCP 工具层认识的候选结构。"""
        return {
            "id": p.get("id"),
            "name": p.get("name", ""),
            "type": "material",
            "score": round(score, 3),
            "extra": {
                "sku": p.get("code", ""),
                "variant": p.get("spec", ""),
                "unit": p.get("unit", ""),
                "stock": p.get("stock", 0),
                "canonical_name": p.get("name", ""),
            },
        }

    @staticmethod
    def _status_label(p: dict) -> str:
        """产品列表接口不带 status，按 stock/minStock 自行推导。"""
        status = p.get("status")
        if isinstance(status, dict) and status.get("label"):
            return status["label"]
        stock = p.get("stock", 0) or 0
        min_stock = p.get("minStock", 0) or 0
        if stock <= 0:
            return "缺货"
        return "库存不足" if stock < min_stock else "正常"

    def _locate(self, product_name: str, fuzzy: bool):
        """解析产品名 → (product, error_response)。歧义时返回澄清响应。"""
        products, err = self._fetch_products()
        if err:
            return None, err

        ranked = self._rank(product_name, products)
        if not ranked:
            return None, {
                "success": False,
                "error": "not_found",
                "message": f"未找到产品：{product_name}",
            }

        top_score, top = ranked[0]

        # 精确命中直接返回
        if top_score >= 0.999:
            return top, None

        if not fuzzy:
            return None, {
                "success": False,
                "error": "not_found",
                "message": f"未精确匹配到产品：{product_name}",
            }

        # 同分并列 / 分数不够 → 让用户澄清，不能替用户猜（写操作尤其危险）
        tied = [x for x in ranked if abs(x[0] - top_score) < 0.02]
        if top_score < _CONFIDENT_SCORE or len(tied) > 1:
            cands = [self._as_candidate(s, p) for s, p in ranked[:6]]
            listed = "、".join(
                f"{c['name']}（{c['extra']['variant'] or c['extra']['sku']}）" for c in cands
            )
            return None, {
                "success": False,
                # 工具层按 ambiguous_name 才会把候选转成"我不确定你说的是哪一个"的追问
                "error": "ambiguous_name",
                "candidates": cands,
                "message": (
                    f"'{product_name}' 匹配到多个产品：{listed}。请告知具体是哪一个"
                    "（可说规格或产品编码）"
                ),
            }

        return top, None

    def _post_movement(self, endpoint: str, product: dict, quantity: int,
                       operator: str, remark: str, qty_key: str) -> dict:
        """调用客户 WMS 的出入库接口并归一化响应。

        qty_key 为 "in_quantity" / "out_quantity" —— MCP 工具层的 slim 响应
        构造器（warehouse_mcp.py 的 stock_in/stock_out 分支）读的是
        product.{in_quantity,out_quantity,new_quantity}，字段名对不上会让
        播报变成"已入库矿泉水?瓶"。
        """
        payload = {
            "productCode": product.get("code", ""),
            "qty": int(quantity),
            "operator": operator or "MCP系统",
            "remark": remark or "",
        }
        if self.warehouse_id:
            payload["warehouse"] = self.warehouse_id
        data = self.http_post(endpoint, payload)

        if not isinstance(data, dict):
            return {"success": False, "error": "api_error", "message": "WMS 返回了无法解析的响应"}

        # 传输层错误（BaseProvider 已归一化为 success=False）
        if data.get("success") is False and "code" not in data:
            return data

        if data.get("code") != 0:
            msg = data.get("msg") or "未知错误"
            # 库存不足是可预期的业务失败，单独给出 error code 便于 LLM 措辞
            error = "insufficient_stock" if "库存不足" in msg else "api_error"
            return {"success": False, "error": error, "message": msg}

        payload = data.get("data") or {}
        prod = payload.get("product") or {}
        tx = payload.get("transaction") or {}
        moved = tx.get("qty", quantity)
        new_stock = prod.get("stock", 0)
        return {
            "success": True,
            "product": {
                "name": prod.get("name", product.get("name", "")),
                "sku": prod.get("code", product.get("code", "")),
                "unit": prod.get("unit", product.get("unit", "")),
                qty_key: moved,
                "new_quantity": new_stock,
                # 保留 current_stock 供直接调用 Provider 的场景使用
                "current_stock": new_stock,
                "status": (prod.get("status") or {}).get("label", ""),
            },
            # 客户 WMS 不做批次管理，显式给空，避免工具层拿到 None 播报"批次号-"以外的噪声
            "batch": {},
            "batch_consumptions": [],
            "transaction_id": tx.get("id", ""),
            "operator": tx.get("operator", operator),
        }

    # ── 1. 模糊名称解析 ──

    def resolve_name(self, text, entity_type="all"):
        # 客户 WMS 只有物料维度，没有联系方/操作员
        if entity_type not in ("all", "material"):
            return {"best_match": None, "confident": False, "candidates": []}

        products, err = self._fetch_products()
        if err:
            return {"best_match": None, "confident": False, "candidates": []}

        ranked = self._rank(text, products)
        if not ranked:
            return {"best_match": None, "confident": False, "candidates": []}

        candidates = [self._as_candidate(s, p) for s, p in ranked[:6]]
        top_score = ranked[0][0]
        tied = [x for x in ranked if abs(x[0] - top_score) < 0.02]
        confident = top_score >= _CONFIDENT_SCORE and len(tied) == 1

        return {
            "best_match": candidates[0] if confident else None,
            "confident": confident,
            "candidates": candidates,
        }

    # ── 2. 库存查询 ──

    def query_stock(self, product_name, show_batches=False):
        product, err = self._locate(product_name, fuzzy=True)
        if err:
            return err

        stock = product.get("stock", 0)
        unit = product.get("unit", "")
        status_label = self._status_label(product)

        result = {
            "success": True,
            "product": {
                "name": product.get("name", ""),
                "sku": product.get("code", ""),
                "current_stock": stock,
                "unit": unit,
                "safe_stock": product.get("minStock", 0),
                "location": product.get("spec", ""),
                "status": status_label,
            },
            "message": (
                f"查询成功：{product.get('name', '')} 当前库存 {stock} {unit}，"
                f"状态：{status_label}"
            ),
        }
        if show_batches:
            # 客户 WMS 不做批次管理，显式返回空列表而不是缺字段
            result["batches"] = []
        return result

    # ── 3. 入库 ──

    def stock_in(self, product_name, quantity, reason_category, reason_note,
                 operator, fuzzy, location=None, contact_id=None,
                 variant=None, allow_new_variant=False, actual_operator=None):
        # 语音里"矿泉水 500ml"的规格会走 variant 参数，拼进查询词提高命中率
        query = f"{product_name}{variant}" if variant else product_name
        product, err = self._locate(query, fuzzy=fuzzy)
        if err:
            return err

        remark = "；".join(x for x in (reason_category, reason_note) if x)
        resp = self._post_movement(
            "/api/inbound", product, quantity, actual_operator or operator,
            remark, "in_quantity",
        )
        if resp.get("success"):
            p = resp["product"]
            resp["message"] = (
                f"入库成功：{p['name']} +{p['in_quantity']} {p['unit']}，"
                f"当前库存 {p['new_quantity']} {p['unit']}"
            )
        return resp

    # ── 4. 出库 ──

    def stock_out(self, product_name, quantity, reason_category, reason_note,
                  operator, fuzzy, variant=None, location=None, batch_no=None,
                  location_fuzzy=False, allow_partial_fallback=False,
                  actual_operator=None):
        if batch_no:
            return {
                "success": False,
                "error": "not_implemented",
                "message": "客户 WMS 未启用批次管理，无法按批次号出库",
            }

        query = f"{product_name}{variant}" if variant else product_name
        product, err = self._locate(query, fuzzy=fuzzy)
        if err:
            return err

        remark = "；".join(x for x in (reason_category, reason_note) if x)
        resp = self._post_movement(
            "/api/outbound", product, quantity, actual_operator or operator,
            remark, "out_quantity",
        )
        if resp.get("success"):
            p = resp["product"]
            resp["message"] = (
                f"出库成功：{p['name']} -{p['out_quantity']} {p['unit']}，"
                f"当前库存 {p['new_quantity']} {p['unit']}"
            )
        return resp

    # ── 5. 统一搜索 ──

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        if entity_type not in ("all", "material"):
            label = {"contact": "联系方", "operator": "操作员"}.get(entity_type, entity_type)
            return {
                "success": False,
                "error": "not_supported",
                "message": f"客户 WMS 不管理{label}数据，无法搜索",
            }

        products, err = self._fetch_products()
        if err:
            return err

        limit = max_results if max_results > 0 else self.max_results

        if query:
            ranked = self._rank(query, products) if fuzzy else [
                (1.0, p) for p in products
                if _norm(query) in _norm(p.get("name")) or _norm(query) == _norm(p.get("code"))
            ]
            matched = [p for _, p in ranked]
        else:
            matched = list(products)

        # status 过滤：normal / low，兼容中文
        if status:
            want = _norm(status)
            alias = {"low": "库存不足", "库存不足": "库存不足", "不足": "库存不足",
                     "normal": "正常", "正常": "正常", "缺货": "缺货", "out": "缺货"}
            target = alias.get(want)
            if target:
                matched = [p for p in matched if self._status_label(p) == target]

        total = len(matched)
        items = [
            {
                "id": p.get("id"),
                "name": p.get("name", ""),
                "sku": p.get("code", ""),
                "spec": p.get("spec", ""),
                "unit": p.get("unit", ""),
                "current_stock": p.get("stock", 0),
                "safe_stock": p.get("minStock", 0),
                "status": self._status_label(p),
            }
            for p in matched[:limit]
        ]

        msg = f"搜索物料成功，找到 {total} 条匹配记录"
        if total > len(items):
            msg += f"（已返回前 {len(items)} 条，可通过 max_results 参数调整上限）"

        return {"success": True, "count": len(items), "total": total,
                "items": items, "message": msg}

    # ── 6. 当天统计 ──

    def get_today_statistics(self):
        today = datetime.now().strftime("%Y-%m-%d")

        tx_data = self.http_get("/api/transactions", params=self._wh_params())
        if not isinstance(tx_data, dict) or tx_data.get("code") != 0:
            msg = (tx_data or {}).get("msg") or "未知错误"
            return {"success": False, "error": "api_error",
                    "message": f"查询统计数据失败: {msg}"}

        today_in = today_out = 0
        for tx in tx_data.get("data") or []:
            # time 形如 "2026-08-04 10:40"
            if not str(tx.get("time", "")).startswith(today):
                continue
            qty = tx.get("qty", 0) or 0
            if tx.get("type") == "in":
                today_in += qty
            elif tx.get("type") == "out":
                today_out += qty

        products, err = self._fetch_products()
        if err:
            return err
        total_stock = sum((p.get("stock", 0) or 0) for p in products)
        low_stock_count = sum(
            1 for p in products if self._status_label(p) in ("库存不足", "缺货")
        )

        return {
            "success": True,
            "date": today,
            "statistics": {
                "today_in": today_in,
                "today_out": today_out,
                "total_stock": total_stock,
                "low_stock_count": low_stock_count,
                "net_change": today_in - today_out,
            },
            "message": (
                f"查询成功：{today} 入库 {today_in} 件，出库 {today_out} 件，"
                f"当前库存总量 {total_stock} 件，库存预警 {low_stock_count} 项"
            ),
        }

    # ── 外部作用域探测（可选方法）──
    # 真实客户系统目前没有这些接口；这里对接 mock 的 /api/orgs /api/warehouses /api/users，
    # 用来验证「探测 → 选择 → 绑定 / 导入」这条链。

    def list_tenants(self):
        data = self.http_get("/api/orgs")
        if not isinstance(data, dict) or data.get("code") != 0:
            return {"success": False, "error": "api_error", "items": [],
                    "message": f"拉取组织失败: {(data or {}).get('msg')}"}
        return {"success": True, "message": "ok",
                "items": [{"id": o["id"], "name": o["name"]} for o in data.get("data") or []]}

    def list_warehouses(self, tenant_id=None):
        params = {"org": tenant_id} if tenant_id else None
        data = self.http_get("/api/warehouses", params=params)
        if not isinstance(data, dict) or data.get("code") != 0:
            return {"success": False, "error": "api_error", "items": [],
                    "message": f"拉取仓库失败: {(data or {}).get('msg')}"}
        return {"success": True, "message": "ok",
                "items": [{"id": w["id"], "name": w["name"]} for w in data.get("data") or []]}

    def list_users(self, tenant_id=None):
        params = {"org": tenant_id} if tenant_id else None
        data = self.http_get("/api/users", params=params)
        if not isinstance(data, dict) or data.get("code") != 0:
            return {"success": False, "error": "api_error", "items": [],
                    "message": f"拉取用户失败: {(data or {}).get('msg')}"}
        return {"success": True, "message": "ok",
                "items": [{"id": u["id"], "name": u["login"], "display_name": u["realName"]}
                          for u in data.get("data") or []]}
