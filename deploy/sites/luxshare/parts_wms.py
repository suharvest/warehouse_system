"""备品管理系统 WMS Provider（按接口文档 v2 重写）

备品系统接口契约（http://10.109.20.102:8888）：
  POST /api/parts_query_stock  <- {method:"parts_query_stock", params:{partType?,partNo?,partName?,stockType?,location?}}
                                 -> {code, data:{partType,partNo,partName,stockType,stockQty,location,unitPrice,safe_stock}, msg}
  POST /api/parts_stock_in     <- {method:"parts_stock_in", params:{partType,number,stockInUser,partNo,partName,location?,UnitPrice?}}
                                 -> {code, data:{partType,OutQty,location,partNo,partName}, msg}
  POST /api/parts_stock_out    <- {method:"parts_stock_out", params:{partType,number,recipient,shift?,partNo?,partName?,remark?}}
                                 -> {code, data:{partType,OutQty,new_quantity}, msg}
  统一响应：code=0 成功，code=1 失败（msg 为原因）

字段说明：
  partType  = 备件型号（接口文档里叫"规格型号"，面向用户统一称"型号"）
  partNo    = 备件编号
  partName  = 备件名称
  location  = 库位

对方接口的硬性要求（2026-08-21 现场实测，与文档不符，以实测为准）：
  parts_query_stock  必须同时带 partType / partNo / partName 三个 key，
                     **值可以是空字符串，但 key 不能缺**——缺任一个对方会抛
                     NullReferenceException（"未将对象引用设置到对象的实例"）。
                     三个全空即为「拉全量」。stockType 可选。
  parts_stock_in     除文档必填项外，还必须带 location 与 UnitPrice；两个都
                     缺则抛同样的空引用（只带其中一个可以通过，但不要赌）。
  parts_stock_out    对字段不敏感。
  响应 data 可能是单条 dict，也可能是 list。

字段映射（照 providers/base.py 的 query_stock 契约，不要再对调）：
  partType（规格型号）→ product.variant   播报成「名称（LH-815）」
  location（库位）    → product.location  播报成「位于 A101」
  播报层不存在 product.spec，写进去会被静默丢弃。

注意：本文件同时要能被两条加载路径导入——
  1. providers/__init__.py 의 _discover()  → 包内相对导入可用
  2. providers/test_runner.load_provider_from_file() → 模块名无父包，相对导入失败
所以 BaseProvider 采用「绝对导入优先、相对导入兜底」的双路径写法。
"""

import difflib
import logging
import re
import time
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
# 库存不足默认阈值（接口返回 safe_stock 时优先用接口值，没有才用此默认）
_LOW_STOCK_THRESHOLD_DEFAULT = 10
# 后端不可达熔断窗口（秒）。设备侧约 10s 收不到音频就断线，后端整体挂掉时
# 逐个接口去撞超时会把一次工具调用拖到十几秒，必然断线；命中传输层错误后
# 直接短路这段时间内的全部请求，让工具调用毫秒级返回可播报的失败话术。
_BACKEND_DOWN_COOLDOWN_SEC = 20
# 后端不可达时对用户播报的话术（播报层 _wrap_response 会把 message 作为 say）
_BACKEND_UNREACHABLE_SAY = "备品系统暂时连不上，请稍后再试或联系管理员"


def _norm(text) -> str:
    """归一化：去空白、转小写、全角转半角，提升 ASR 文本的匹配率。"""
    if not text:
        return ""
    s = str(text).strip().lower()
    s = s.translate(str.maketrans({"－": "-", "–": "-", "—": "-", "　": " "}))
    return re.sub(r"\s+", "", s)


# ── ASR 语音误识别归一化（2026-09-16 补丁）──
# 只作用于「用户输入」一侧，不改评分/阈值/候选逻辑。
# 「杠/横杠/斜杠/破折号/减号」等口播读法 → '-'
_ASR_DASH_WORDS = ("横杠", "斜杠", "破折号", "减号", "杠")
# 中文数字逐字读法 → 阿拉伯数字（一零→10、一零零→100、零二→02）
_ASR_CN_DIGITS = {
    "零": "0", "一": "1", "二": "2", "两": "2", "三": "3",
    "四": "4", "五": "5", "六": "6", "七": "7", "八": "8",
    "九": "9", "十": "10",
}
# 同音/误听词表（可配置，按需追加）：左=误识别，右=正确写法
_ASR_SYNONYMS = {
    "司通": "四通",
    "丝通": "四通",
}
# 口播标点，匹配前剥离
_ASR_PUNCT = "，。、；：？！,.;:!?·\"'“”‘’（）()【】[]~～"


def _asr_normalize(text) -> str:
    """对用户查询做 ASR 归一化：标点剥离 → 口播符号词 → 中文数字 → 同音词。

    中文数字只在「与 ASCII 字母/数字/连字符相邻」的数字串内转换，
    避免把「四通」误转成「4通」。
    """
    if not text:
        return ""
    s = str(text).strip()
    # 1. 剥离标点（保留原始空白交给 _norm 处理）
    s = s.translate(str.maketrans("", "", _ASR_PUNCT))
    # 2. 口播符号词 → '-'（长词优先），并去重连续 '-'
    for w in _ASR_DASH_WORDS:
        s = s.replace(w, "-")
    s = re.sub(r"-{2,}", "-", s)
    # 3. 中文数字逐字转换。规则（任一满足才转，避免误伤「四通」）：
    #    a. 相邻字符是 '-' 或 ASCII 字母；
    #    b. 后一个字符仍是中文/ASCII 数字（数字串中间）；
    #    c. 前一个是中文数字且后面不是汉字词（数字串结尾，如「零二」→「02」）。
    #    反例：「10-8四通」的「四」前是 ASCII 数字、后是汉字 → 保留。
    cn = set(_ASR_CN_DIGITS)

    def _is_cjk(ch):
        return "\u4e00" <= ch <= "\u9fff"

    chars = list(s)
    out = list(chars)
    for i, ch in enumerate(chars):
        if ch not in cn:
            continue
        prev = chars[i - 1] if i > 0 else ""
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if (prev and re.match(r"[A-Za-z\-]", prev)) or (
            nxt and re.match(r"[A-Za-z\-]", nxt)
        ):
            out[i] = _ASR_CN_DIGITS[ch]            # 规则 a
        elif nxt and (nxt in cn or nxt.isascii() and nxt.isdigit()):
            out[i] = _ASR_CN_DIGITS[ch]            # 规则 b
        elif prev in cn and not (nxt and _is_cjk(nxt) and nxt not in cn):
            out[i] = _ASR_CN_DIGITS[ch]            # 规则 c
    s = "".join(out)
    # 4. 同音/误听词替换
    for wrong, right in _ASR_SYNONYMS.items():
        s = s.replace(wrong, right)
    return s


def _norm_query(text) -> str:
    """查询词专用归一化 = ASR 归一化 + 通用 _norm。"""
    return _norm(_asr_normalize(text))


def _safe_stock_of(part: dict, fallback: int) -> int:
    """从备品数据中取安全库存，接口有 safe_stock 就用，没有用 fallback。"""
    v = part.get("safe_stock")
    if v is not None:
        try:
            return int(v)
        except (ValueError, TypeError):
            pass
    return fallback


class PartsWmsProvider(BaseProvider):
    """备品管理系统 WMS 适配器。"""

    PROVIDER_NAME = "parts_wms"

    def __init__(self, config: dict):
        super().__init__(config)
        self.warehouse_id = config.get("warehouse_id", "default")
        self.max_results = int(config.get("max_results", 10))
        self.low_stock_threshold = int(
            config.get("low_stock_threshold", _LOW_STOCK_THRESHOLD_DEFAULT)
        )
        # 后端不可达熔断截止时间（time.monotonic() 刻度，0 表示未熔断）
        self._backend_down_until = 0.0
        logger.info(
            "[PartsWms] 初始化完成, warehouse_id=%s, max_results=%d, "
            "low_stock_threshold=%d",
            self.warehouse_id, self.max_results, self.low_stock_threshold,
        )

    # ── 内部工具 ──

    @staticmethod
    def _backend_unreachable_resp() -> dict:
        """后端不可达的统一返回体。

        ``success/error/message`` 是 Provider 与 MCP 播报层的约定字段，
        ``ok/executed/say/say_kind`` 是播报层对外 schema 的同名字段，
        一并带上，直接调用 Provider 的场景也能拿到可播报的话术。
        """
        return {
            "success": False,
            "error": "backend_unreachable",
            "message": _BACKEND_UNREACHABLE_SAY,
            "ok": False,
            "executed": False,
            "say": _BACKEND_UNREACHABLE_SAY,
            "say_kind": "tell",
        }

    @staticmethod
    def _is_backend_down(result) -> bool:
        return isinstance(result, dict) and result.get("error") == "backend_unreachable"

    def _call(self, method: str, params: dict | None = None) -> dict:
        """调用备品系统 JSON-RPC 风格接口，统一处理响应。"""
        # 熔断窗口内不再发请求，直接返回可播报的失败结果
        remaining = self._backend_down_until - time.monotonic()
        if remaining > 0:
            logger.warning(
                "[PartsWms] 后端不可达熔断中，跳过接口 %s: url=%s, 剩余 %.1fs",
                method, self.base_url, remaining,
            )
            return self._backend_unreachable_resp()

        endpoint = f"/api/{method}"
        payload = {"method": method, "params": params or {}}
        logger.debug("[PartsWms] 调用接口 %s, params=%s", method, params)
        data = self.http_post(endpoint, payload)

        # 传输层错误（BaseProvider 已归一化为 success=False）
        # 连接被拒 / 超时 / DNS 失败都走这里：立刻熔断，不做任何单点退化查询。
        if isinstance(data, dict) and data.get("success") is False and "code" not in data:
            self._backend_down_until = time.monotonic() + _BACKEND_DOWN_COOLDOWN_SEC
            logger.warning(
                "[PartsWms] 接口 %s 传输层错误: %s；后端不可达，熔断 %ds（url=%s）",
                method, data.get("message", "未知"),
                _BACKEND_DOWN_COOLDOWN_SEC, self.base_url,
            )
            return self._backend_unreachable_resp()

        if not isinstance(data, dict):
            logger.error("[PartsWms] 接口 %s 返回非字典响应: %s", method, data)
            return {
                "success": False,
                "error": "api_error",
                "message": "备品系统返回了无法解析的响应",
            }

        if data.get("code") != 0:
            msg = data.get("msg") or "未知错误"
            error = "insufficient_stock" if "库存不足" in msg else "api_error"
            logger.warning(
                "[PartsWms] 接口 %s 业务失败, code=%s, msg=%s",
                method, data.get("code"), msg,
            )
            return {"success": False, "error": error, "message": msg}

        logger.debug("[PartsWms] 接口 %s 调用成功", method)
        return {"success": True, "data": data.get("data") or {}, "msg": data.get("msg", "")}

    @staticmethod
    def _query_params(**kw) -> dict:
        """构造 parts_query_stock 的 params。

        三个 key 恒定存在（缺 key 对方抛 NullReferenceException），只把要查的
        那个填上值。三个全空即为拉全量。
        """
        params = {"partType": "", "partNo": "", "partName": ""}
        params.update({k: v for k, v in kw.items() if v is not None})
        return params

    def _fetch_products(self):
        """拉取全量备品列表，返回 (products, error_response)。

        备品系统没有专门的列表接口，通过不带过滤参数的库存查询尝试获取全量。
        如果返回单条则包成列表，返回列表直接使用。
        """
        logger.info("[PartsWms] 拉取全量备品列表...")
        result = self._call("parts_query_stock", self._query_params())
        if self._is_backend_down(result):
            # 后端不可达：不再退化为单点查询，直接把可播报的失败话术抛上去
            return None, self._backend_unreachable_resp()
        if not result.get("success"):
            err_msg = f"访问备品系统失败: {result.get('message', '未知错误')}"
            logger.error("[PartsWms] 全量拉取失败: %s", err_msg)
            return None, {
                "success": False,
                "error": result.get("error", "api_error"),
                "message": err_msg,
            }

        raw = result.get("data") or {}
        # data 可能是单条对象，也可能是列表（接口文档示例为单条，不传参时可能返回列表）
        if isinstance(raw, list):
            logger.info("[PartsWms] 全量拉取成功，共 %d 条", len(raw))
            return raw, None
        if isinstance(raw, dict) and raw:
            logger.info("[PartsWms] 全量拉取返回单条，包装为列表")
            return [raw], None
        # 空数据返回空列表
        logger.warning("[PartsWms] 全量拉取返回空数据（接口可能不支持无参数全量查询）")
        return [], None

    def _score(self, query: str, part: dict) -> float:
        """给单个备品打匹配分（0~1）。名称/编号/型号三路取最大值。"""
        q = _norm_query(query)
        if not q:
            return 0.0

        best = 0.0
        name = _norm(part.get("partName"))
        no = _norm(part.get("partNo"))
        ptype = _norm(part.get("partType"))

        # 编号完全一致 → 直接满分
        if no and q == no:
            return 1.0
        # 名称完全一致 → 满分
        if name and q == name:
            return 1.0
        # 型号完全一致 → 满分
        if ptype and q == ptype:
            return 1.0
        # "撬具LH-815" 这类「名称+型号」连读
        if name and ptype and q == name + ptype:
            return 1.0
        if ptype and name and q == ptype + name:
            return 1.0

        for field, weight in ((name, 1.0), (no, 0.9), (ptype, 0.8)):
            if not field:
                continue
            if q in field or field in q:
                ratio = min(len(q), len(field)) / max(len(q), len(field))
                best = max(best, weight * (0.72 + 0.28 * ratio))
            best = max(best, weight * difflib.SequenceMatcher(None, q, field).ratio())
        return best

    def _rank(self, query: str, products: list) -> list:
        """按匹配分降序返回 [(score, part), ...]，已过滤低分噪声。"""
        scored = [(self._score(query, p), p) for p in products]
        scored = [x for x in scored if x[0] >= _CANDIDATE_FLOOR]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    @staticmethod
    def _as_candidate(score: float, p: dict) -> dict:
        """转成 MCP 工具层认识的候选结构。"""
        return {
            "id": p.get("partNo"),
            "name": p.get("partName", ""),
            "type": "material",
            "score": round(score, 3),
            "extra": {
                "sku": p.get("partNo", ""),
                "variant": p.get("partType", ""),
                "unit": "件",
                "stock": p.get("stockQty", 0),
                "canonical_name": p.get("partName", ""),
                "location": p.get("location", ""),
            },
        }

    def _status_label(self, p: dict) -> str:
        """按 stockQty 与阈值推导状态。优先用接口返回的 safe_stock。"""
        stock = p.get("stockQty", 0) or 0
        threshold = _safe_stock_of(p, self.low_stock_threshold)
        if stock <= 0:
            return "缺货"
        return "库存不足" if stock < threshold else "正常"

    def _single_point_lookup(self, text: str):
        """全量列表不可用时的单点兜底查询，返回命中的 part dict 或 None。

        两条硬教训：

        1. **失败不能提前返回。** 原先是先查 partName，只要这一步 code!=0 就
           直接判 not_found —— 后面那句"再按型号试一次"永远执行不到。现场
           parts_query_stock 抛 NullReferenceException 时，按型号本来能查到的
           物料也一并查不到了。
        2. **顺序要看查询词形态。** 用户报 "100201" 这种纯编码时那是型号
           (partType)，先按名称查必然落空，还会让对方日志里出现
           {"partName":"100201"} 这种把型号当名称的请求。
        """
        text = _asr_normalize(text)
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text or ""))
        order = ("partName", "partType") if has_cjk else ("partType", "partName")
        for field in order:
            result = self._call("parts_query_stock", self._query_params(**{field: text}))
            if self._is_backend_down(result):
                logger.warning("[PartsWms] 单点查询放弃：后端不可达")
                return None
            if not result.get("success"):
                logger.warning(
                    "[PartsWms] 单点查询(%s=%s) 失败: %s，继续尝试其他字段",
                    field, text, result.get("message"),
                )
                continue
            data = result.get("data") or {}
            if isinstance(data, list):
                data = data[0] if data else {}
            if isinstance(data, dict) and data:
                logger.info("[PartsWms] 单点命中(%s): %s（%s）",
                            field, data.get("partName"), data.get("partType"))
                return data
        return None

    def _locate(self, product_name: str, fuzzy: bool):
        """解析备品名 → (part, error_response)。歧义时返回澄清响应。

        优先用全量列表做模糊匹配；如果全量拉取失败，退化为单点查询。
        """
        logger.info("[PartsWms] 定位备品: query='%s', fuzzy=%s", product_name, fuzzy)
        products, err = self._fetch_products()
        if self._is_backend_down(err):
            return None, err
        if err:
            # 全量拉取失败，退化为按名称单点查询
            logger.warning(
                "[PartsWms] 全量列表不可用，退化为单点查询（按名称）: %s",
                product_name,
            )
            hit = self._single_point_lookup(product_name)
            if hit:
                return hit, None
            return None, {
                "success": False,
                "error": "not_found",
                "message": f"未找到备品：{product_name}",
            }

        if not products:
            logger.warning("[PartsWms] 备品列表为空，无法匹配")
            return None, {
                "success": False,
                "error": "not_found",
                "message": f"未找到备品：{product_name}",
            }

        ranked = self._rank(product_name, products)
        if not ranked:
            logger.info("[PartsWms] 无匹配结果: %s", product_name)
            return None, {
                "success": False,
                "error": "not_found",
                "message": f"未找到备品：{product_name}",
            }

        top_score, top = ranked[0]
        logger.info(
            "[PartsWms] 匹配结果 top1: %s（%s）, 分数=%.3f",
            top.get("partName"), top.get("partType"), top_score,
        )

        # 并列判定必须在"精确命中"之前算 —— 备品系统里存在同名不同型号的记录
        # （现场两条都叫「探针」，分属 100201 与 L101-JT0.5*3-H3）。按名称查时
        # 两条都是满分，原先 `top_score >= 0.999 直接返回` 会跳过下面的歧义检查、
        # 静默取排第一的那条，把入库记到错误型号上。
        tied = [x for x in ranked if abs(x[0] - top_score) < 0.02]

        # 精确命中「且唯一」才直接返回
        if top_score >= 0.999 and len(tied) == 1:
            return top, None

        if not fuzzy:
            return None, {
                "success": False,
                "error": "not_found",
                "message": f"未精确匹配到备品：{product_name}",
            }

        # 同分并列 / 分数不够 → 让用户澄清（tied 已在上面算过）
        if top_score < _CONFIDENT_SCORE or len(tied) > 1:
            cands = [self._as_candidate(s, p) for s, p in ranked[:6]]
            listed = "、".join(
                f"{c['name']}（{c['extra']['variant'] or c['extra']['sku']}）" for c in cands
            )
            logger.info(
                "[PartsWms] 需要用户澄清: top_score=%.3f, 候选数=%d",
                top_score, len(cands),
            )
            return None, {
                "success": False,
                "error": "ambiguous_name",
                "candidates": cands,
                "message": (
                    f"'{product_name}' 匹配到多个备品：{listed}。请告知具体是哪一个"
                    "（可说型号或备件编号）"
                ),
            }

        return top, None

    def _post_movement(self, method: str, part: dict, quantity: int,
                       operator: str, remark: str, qty_key: str,
                       extra_params: dict | None = None) -> dict:
        """调用备品系统出入库接口并归一化响应。"""
        params = {
            "partType": part.get("partType", ""),
            "number": int(quantity),
        }
        if extra_params:
            params.update(extra_params)

        result = self._call(method, params)
        if not result.get("success"):
            return result

        data = result.get("data") or {}
        moved = data.get("OutQty", quantity)

        # 最新库存：优先用接口返回的 new_quantity（出库有），没有就用当前库存推算
        if "new_quantity" in data and data["new_quantity"] is not None:
            new_stock = data["new_quantity"]
        else:
            current_stock = part.get("stockQty", 0) or 0
            if method == "parts_stock_in":
                new_stock = current_stock + moved
            else:
                new_stock = max(0, current_stock - moved)

        # 出入库响应里可能带回 partNo/partName/location/partType，优先用返回值
        resp_name = data.get("partName") or part.get("partName", "")
        resp_sku = data.get("partNo") or part.get("partNo", "")
        resp_type = data.get("partType") or part.get("partType", "")
        # 物理库位
        resp_storage = data.get("location") or part.get("location", "")


        return {
            "success": True,
            "product": {
                "name": resp_name,
                "sku": resp_sku,
                "unit": "件",
                qty_key: moved,
                "new_quantity": new_stock,
                "current_stock": new_stock,
                "variant": resp_type,
                "location": resp_storage,
                # 带上安全库存，出库后跌破安全线时播报层会当场追加提醒
                # （不带的话只有查询会提醒，出库完悄无声息）。
                "safe_stock": _safe_stock_of(part, self.low_stock_threshold),
                "status": "",
            },
            "batch": {},
            "batch_consumptions": [],
            "transaction_id": "",
            "operator": operator,
        }

    # ── 1. 模糊名称解析 ──

    def resolve_name(self, text, entity_type="all"):
        # 备品系统只有物料维度，没有联系方/操作员
        if entity_type not in ("all", "material"):
            return {"best_match": None, "confident": False, "candidates": []}

        logger.info("[PartsWms] resolve_name: text='%s', entity_type=%s", text, entity_type)
        products, err = self._fetch_products()
        if self._is_backend_down(err):
            logger.warning("[PartsWms] resolve_name 放弃：后端不可达")
            return {"best_match": None, "confident": False, "candidates": []}
        if err:
            # 全量拉取失败，退化为单点查询
            logger.warning("[PartsWms] resolve_name 退化为单点查询")
            hit = self._single_point_lookup(text)
            if hit:
                cand = self._as_candidate(1.0, hit)
                return {"best_match": cand, "confident": True, "candidates": [cand]}
            return {"best_match": None, "confident": False, "candidates": []}

        ranked = self._rank(text, products)
        if not ranked:
            return {"best_match": None, "confident": False, "candidates": []}

        candidates = [self._as_candidate(s, p) for s, p in ranked[:6]]
        top_score = ranked[0][0]
        tied = [x for x in ranked if abs(x[0] - top_score) < 0.02]
        confident = top_score >= _CONFIDENT_SCORE and len(tied) == 1

        logger.info(
            "[PartsWms] resolve_name 结果: confident=%s, top_score=%.3f, 候选数=%d",
            confident, top_score, len(candidates),
        )
        return {
            "best_match": candidates[0] if confident else None,
            "confident": confident,
            "candidates": candidates,
        }

    # ── 2. 库存查询 ──

    def query_stock(self, product_name, show_batches=False):
        logger.info("[PartsWms] query_stock: product_name='%s'", product_name)
        part, err = self._locate(product_name, fuzzy=True)
        if err:
            return err

        stock = part.get("stockQty", 0)
        status_label = self._status_label(part)
        safe_stock = _safe_stock_of(part, self.low_stock_threshold)
        part_type = part.get("partType", "")
        # 物理库位
        storage_loc = part.get("location", "") or ""


        result = {
            "success": True,
            "product": {
                "name": part.get("partName", ""),
                "sku": part.get("partNo", ""),
                "variant": part_type,
                "current_stock": stock,
                "unit": "件",
                "safe_stock": safe_stock,
                "location": storage_loc,
                "status": status_label,
            },
            "message": (
                f"查询成功：{part.get('partName', '')}"
                f"（型号：{part_type}）"
                f"当前库存 {stock} 件，状态：{status_label}"
                + (f"，库位：{storage_loc}" if storage_loc else "")
            ),
        }
        if show_batches:
            result["batches"] = []
        logger.info(
            "[PartsWms] query_stock 完成: %s, stock=%s, status=%s",
            part.get("partName"), stock, status_label,
        )
        return result

    # ── 3. 入库 ──

    def stock_in(self, product_name, quantity, reason_category, reason_note,
                 operator, fuzzy, location=None, contact_id=None,
                 variant=None, allow_new_variant=False, actual_operator=None):
        query = f"{product_name}{variant}" if variant else product_name
        logger.info(
            "[PartsWms] stock_in: query='%s', qty=%d, operator=%s",
            query, quantity, actual_operator or operator,
        )
        part, err = self._locate(query, fuzzy=fuzzy)
        if err:
            return err

        # 备品入库必填：partType, number, stockInUser, partNo, partName
        # 选填：location, UnitPrice
        # location / UnitPrice 必须恒定带上：两个都缺时对方抛空引用异常。
        # 用户没指定库位就回填该备品在对方系统里已有的库位，不凭空编一个；
        # 单价同理，取对方记录里的值，没有才退 0。
        extra = {
            "stockInUser": actual_operator or operator or "WMS",
            "partNo": part.get("partNo", ""),
            "partName": part.get("partName", ""),
            "location": location or part.get("location", "") or "",
            "UnitPrice": part.get("unitPrice", 0) or 0,
        }
        if reason_note:
            extra["remark"] = reason_note

        resp = self._post_movement(
            "parts_stock_in", part, quantity,
            actual_operator or operator or "WMS",
            reason_note or "", "in_quantity", extra,
        )
        if resp.get("success"):
            p = resp["product"]
            loc_info = f"，库位：{p.get('location', '')}" if p.get("location") else ""
            resp["message"] = (
                f"入库成功：{p['name']}（型号：{part.get('partType', '')}）"
                f"+{p['in_quantity']} 件，当前库存约 {p['new_quantity']} 件{loc_info}"
            )
            logger.info(
                "[PartsWms] stock_in 完成: %s, +%d, 库存=%s",
                p["name"], p["in_quantity"], p["new_quantity"],
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
                "message": "备品系统未启用批次管理，无法按批次号出库",
            }

        query = f"{product_name}{variant}" if variant else product_name
        logger.info(
            "[PartsWms] stock_out: query='%s', qty=%d, operator=%s",
            query, quantity, actual_operator or operator,
        )
        part, err = self._locate(query, fuzzy=fuzzy)
        if err:
            return err

        # 备品出库必填：partType, number, recipient
        # 选填：shift, partNo, partName, remark
        extra = {
            "recipient": actual_operator or operator or "WMS",
            "partNo": part.get("partNo", ""),
            "partName": part.get("partName", ""),
        }
        remark = "；".join(x for x in (reason_category, reason_note) if x)
        if remark:
            extra["remark"] = remark

        resp = self._post_movement(
            "parts_stock_out", part, quantity,
            actual_operator or operator or "WMS",
            remark, "out_quantity", extra,
        )
        if resp.get("success"):
            p = resp["product"]
            resp["message"] = (
                f"出库成功：{p['name']}（型号：{part.get('partType', '')}）"
                f"-{p['out_quantity']} 件，当前库存 {p['new_quantity']} 件"
            )
            logger.info(
                "[PartsWms] stock_out 完成: %s, -%d, 库存=%s",
                p["name"], p["out_quantity"], p["new_quantity"],
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
                "message": f"备品系统不管理{label}数据，无法搜索",
            }

        logger.info(
            "[PartsWms] search: query='%s', entity_type=%s, status=%s, fuzzy=%s",
            query, entity_type, status, fuzzy,
        )
        products, err = self._fetch_products()
        if err:
            return err

        limit = max_results if max_results > 0 else self.max_results

        if query:
            ranked = self._rank(query, products) if fuzzy else [
                (1.0, p) for p in products
                if _norm(query) in _norm(p.get("partName"))
                   or _norm(query) == _norm(p.get("partNo"))
                   or _norm(query) == _norm(p.get("partType"))
            ]
            matched = [p for _, p in ranked]
        else:
            matched = list(products)

        # status 过滤
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
                "id": p.get("partNo"),
                "name": p.get("partName", ""),
                "sku": p.get("partNo", ""),
                "variant": p.get("partType", ""),
                "unit": "件",
                "current_stock": p.get("stockQty", 0),
                "safe_stock": _safe_stock_of(p, self.low_stock_threshold),
                "location": p.get("location", "") or "",
                "status": self._status_label(p),
            }
            for p in matched[:limit]
        ]

        msg = f"搜索备品成功，找到 {total} 条匹配记录"
        if total > len(items):
            msg += f"（已返回前 {len(items)} 条，可通过 max_results 参数调整上限）"

        logger.info("[PartsWms] search 完成: 匹配 %d 条, 返回 %d 条", total, len(items))
        return {"success": True, "count": len(items), "total": total,
                "items": items, "message": msg}

    # ── 6. 当天统计 ──

    def get_today_statistics(self):
        """备品系统没有交易记录查询接口，返回库存快照统计供框架校验。"""
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info("[PartsWms] get_today_statistics: date=%s", today)

        # 从库存查询获取总量快照（无交易记录接口，今日出入库存 0）
        products, _ = self._fetch_products()
        total_stock = 0
        low_stock_count = 0
        if products:
            total_stock = sum((p.get("stockQty", 0) or 0) for p in products)
            low_stock_count = sum(
                1 for p in products if self._status_label(p) in ("库存不足", "缺货")
            )

        result = {
            "success": True,
            "date": today,
            "statistics": {
                "today_in": 0,
                "today_out": 0,
                "total_stock": total_stock,
                "low_stock_count": low_stock_count,
                "net_change": 0,
            },
            "message": (
                f"当前库存总量 {total_stock} 件，库存预警 {low_stock_count} 项"
                "（备品系统暂未提供交易记录接口，今日出入库数据暂不可用）"
            ),
        }
        logger.info(
            "[PartsWms] get_today_statistics 完成: total_stock=%d, low_stock_count=%d",
            total_stock, low_stock_count,
        )
        return result
