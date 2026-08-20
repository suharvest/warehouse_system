"""Provider 公共匹配层 —— 「拉全量 + 本地打分」这套逻辑的唯一实现。

## 为什么需要这一层

对接外部 WMS 的 Provider 都是同一个模式：从对方接口拉回物料列表，在我们
这边打分排序，命中就返回、分数不够就让用户澄清。这套逻辑与对方是谁无关，
但在重构前每个 Provider 都自己写了一遍：

    parts_wms.py          _norm/_score/_rank/_as_candidate/_locate   ~130 行
    mock_wms/provider.py  _norm/_score/_rank/_as_candidate/_locate   ~130 行

两份实现逐行相似，差异只有字段名（``partName`` vs ``name``）和规格字段的
权重。而本地模式 ``DefaultProvider`` 走服务端 ``FuzzyMatcher``，用的是
rapidfuzz + pypinyin，能力又比这两份 difflib 强一截 —— 同一句话在三条路径
上表现不同，且没人能说清差在哪。

这一层把打分统一到 ``FuzzyMatcher`` 的能力上（拼音容错 + rapidfuzz），
Provider 只需要回答两个问题：**去哪拉数据**、**哪个字段是名称/编码/规格**。

## 分数尺度

对外是 0~1，与既有 Provider 的阈值（``CONFIDENT_SCORE=0.75`` /
``CANDIDATE_FLOOR=0.34``）保持一致，重构不改变阈值语义。内部计算按
``FuzzyMatcher`` 的 0~100 走，最后除以 100 —— 这样两边的打分公式可以逐行
对照，改一处时另一处的偏差能被 tests/test_provider_matching.py 里的一致性
用例抓到。
"""

import logging
import re
import threading
import time
from collections import OrderedDict

from pypinyin import Style, lazy_pinyin
from rapidfuzz import fuzz

from .normalize import cn_digits_to_arabic

logger = logging.getLogger("WarehouseMCP")

__all__ = ["MatchConfig", "LocalMatchMixin", "norm_for_match", "tokenize_for_match"]


# 拼音缓存上限，超过后 LRU 淘汰。与 backend/fuzzy_match.py 的 _PINYIN_CACHE_MAX
# 同值：两边缓存的是同一类文本（物料名），容量没有分开调的理由。
_PINYIN_CACHE_MAX = 10000

# 拼音分的采信门槛（0~100 文本分尺度）。低于此值说明查询词和该字段字面
# 毫不相关，此时的拼音相似度纯属拉丁字母的偶然重叠，不能采信。
# 标定依据见 _score 里的注释和 tests/test_provider_matching.py 的噪声用例。
_PINYIN_TEXT_GATE = 30.0

_pinyin_cache: "OrderedDict[str, str]" = OrderedDict()
_pinyin_lock = threading.Lock()


def norm_for_match(text) -> str:
    """匹配用归一化：去干扰字符 → 中文数字还原 → 小写。

    与 ``FuzzyMatcher._normalize`` 逐字对齐。注意这里**会**去掉横杠括号等
    标点，和 ``normalize.normalize_query``（工具入口那层）的边界不同 ——
    那层的结果要原样发给外部 ERP 做精确查询，不能破坏字面值；这层只用于
    本地打分，去标点能让 "4IO-2.0" 和 "4IO2.0" 对上。
    """
    if not text:
        return ""
    t = re.sub(r'[\s\-－/／\(\)（）\[\]【】,，、]+', '', str(text))
    return cn_digits_to_arabic(t).lower()


def tokenize_for_match(text) -> str:
    """中英文边界补空格 + 折叠空格，供 token_set_ratio 切词。

    与 ``FuzzyMatcher._tokenize`` 逐字对齐。口语 "银色M3螺丝" 没有空格，
    rapidfuzz 无法切分会退化成单 token；补空格后能和索引项 "M3 螺丝 银色"
    匹配上。中文数字还原必须在补空格之前 —— 转成阿拉伯数字后才有中↔ASCII
    边界。
    """
    if not text:
        return ""
    t = cn_digits_to_arabic(str(text))
    t = re.sub(r'([一-鿿])([A-Za-z0-9])', r'\1 \2', t)
    t = re.sub(r'([A-Za-z0-9])([一-鿿])', r'\1 \2', t)
    t = re.sub(r'[\-－/／\(\)（）\[\]【】,，、]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip().lower()


def _pinyin(text: str) -> str:
    """无声调拼音串，带进程级 LRU 缓存。

    缓存是模块级而非实例级：Provider 会随连接反复创建，实例级缓存等于没有。
    物料名的基数有限，进程级共享命中率高得多。
    """
    if not text:
        return ""
    with _pinyin_lock:
        hit = _pinyin_cache.get(text)
        if hit is not None:
            _pinyin_cache.move_to_end(text)
            return hit
    # 计算放在锁外，不阻塞读路径
    result = ' '.join(lazy_pinyin(text, style=Style.NORMAL))
    with _pinyin_lock:
        _pinyin_cache[text] = result
        _pinyin_cache.move_to_end(text)
        while len(_pinyin_cache) > _PINYIN_CACHE_MAX:
            _pinyin_cache.popitem(last=False)
    return result


class MatchConfig:
    """一个 Provider 的匹配参数。子类通过 ``MATCH`` 类属性覆盖。

    ``fields`` 把 Provider 的原始记录映射成三个语义角色，值是记录里的键名：

        MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})

    ``weights`` 是各角色的分数权重。名称权重最高（用户最常报名称），编码次之，
    规格最低 —— 规格串（"4IO-2.0-3.2-12-A"）字符多且相似度高，权重给满会让
    不同物料互相干扰。
    """

    def __init__(self, fields: dict, weights: dict | None = None,
                 confident_score: float = 0.75, candidate_floor: float = 0.34,
                 tie_epsilon: float = 0.02):
        self.fields = dict(fields)
        self.weights = {"name": 1.0, "code": 0.9, "spec": 0.8}
        if weights:
            self.weights.update(weights)
        self.confident_score = confident_score
        self.candidate_floor = candidate_floor
        self.tie_epsilon = tie_epsilon


# 全量列表缓存的默认存活时间（秒）。
#
# 这个值可以设得比较宽松，因为「查不到就强制刷新重试」兜住了实时性：
# 用户新增物料后立刻查询，第一次在旧缓存里匹配不上，会触发刷新后重试，
# 当场就能查到，不需要等 TTL 到期。TTL 只决定「多久无条件重拉一次」，
# 用来兜住改名、删除这类不会表现为 not_found 的变更。
_DEFAULT_CACHE_TTL = 60.0

# 两次强制刷新之间的最小间隔（秒），防止 not_found 风暴。
#
# 用户连问几个库里根本没有的东西时，每次都会触发强制刷新。没有这个防抖，
# 一轮对话里 5 次工具调用就是 5 次全量拉取，对方 ERP 会被打爆。
_REFRESH_DEBOUNCE = 5.0


class _CacheEntry:
    __slots__ = ("items", "indexed", "fetched_at", "last_refresh_attempt")

    def __init__(self, items, indexed, fetched_at):
        self.items = items
        self.indexed = indexed
        self.fetched_at = fetched_at
        # 初始值要让「首次强制刷新」立刻可用。若设成 fetched_at，刚建好缓存
        # 的 _REFRESH_DEBOUNCE 秒内就不许强制刷新 —— 而「用户新增物料后立刻
        # 查询」恰好落在这个窗口里，正是最需要刷新的时刻。
        self.last_refresh_attempt = fetched_at - _REFRESH_DEBOUNCE


class _ItemCache:
    """按 Provider 身份隔离的全量列表缓存。

    进程级而非实例级 —— Provider 会随每个 MCP 连接重新创建，实例级缓存等于
    没有。key 必须含租户/仓库标识，否则多租户部署会互相看到对方的数据。
    """

    def __init__(self):
        self._data: dict = {}
        self._lock = threading.RLock()

    def get(self, key):
        with self._lock:
            return self._data.get(key)

    def put(self, key, items, indexed, now, refreshed=False):
        """写入缓存。

        ``refreshed=True`` 表示这次是「未命中触发的强制刷新」，要把防抖
        时间戳设成现在 —— 否则新建的 _CacheEntry 会把刚打上的标记冲掉，
        防抖形同虚设（实测连问 5 个不存在的词会拉 5 次全量）。
        普通的 TTL 到期刷新不占用防抖额度。
        """
        with self._lock:
            old = self._data.get(key)
            e = _CacheEntry(items, indexed, now)
            if refreshed:
                e.last_refresh_attempt = now
            elif old is not None:
                e.last_refresh_attempt = old.last_refresh_attempt
            self._data[key] = e
            return e

    def invalidate(self, key=None):
        with self._lock:
            if key is None:
                self._data.clear()
            else:
                self._data.pop(key, None)



_item_cache = _ItemCache()


class _Indexed:
    """一条记录的预计算结果。

    归一化、分词、拼音在拉取时算一次存下来，打分时直接用 —— 实测比每次
    重算快 3.5 倍（10000 条：300ms → 85ms）。
    """

    __slots__ = ("raw", "fields")

    def __init__(self, raw: dict, field_map: dict):
        self.raw = raw
        self.fields = {}
        for role, key in field_map.items():
            if not key:
                continue
            v = raw.get(key)
            n = norm_for_match(v)
            self.fields[role] = (n, tokenize_for_match(v), _pinyin(n))


class _Query:
    """一次查询的预计算结果，避免对每条记录重复算归一化和拼音。"""

    __slots__ = ("raw", "norm", "tokens", "pinyin")

    def __init__(self, raw: str):
        self.raw = raw
        self.norm = norm_for_match(raw)
        self.tokens = tokenize_for_match(raw)
        self.pinyin = _pinyin(self.norm)


class LocalMatchMixin:
    """给「拉全量 + 本地匹配」型 Provider 用的公共实现。

    子类必须提供：

    * ``MATCH``：``MatchConfig`` 实例，说明字段怎么映射
    * ``_fetch_items()``：返回 ``(items, error_response)``，拉全量列表

    子类可选覆盖：

    * ``_as_candidate(score, item)``：候选项的对外结构，默认按 fields 映射
    * ``_not_found(query)`` / ``_ambiguous(query, candidates)``：错误响应文案

    覆盖了 ``_score`` / ``_rank`` / ``_locate`` 的老 Provider 行为不变 ——
    Python 的 MRO 让子类实现优先，这一层是纯增量，不破坏既有对接。
    """

    MATCH: MatchConfig = MatchConfig(fields={"name": "name", "code": "code", "spec": "spec"})

    # ── 打分 ──

    def _field_values(self, item: dict) -> dict:
        """按 MATCH.fields 把记录映射成 {role: 归一化后的值}。"""
        out = {}
        for role, key in self.MATCH.fields.items():
            if key:
                out[role] = norm_for_match(item.get(key))
        return out

    def _score(self, query, item: dict) -> float:
        """给单条记录打分，0~1。

        打分公式对齐 ``FuzzyMatcher._calc_score``（那边是 0~100，这里最后
        除以 100）：精确相等 → 满分；子串包含 → 按长度比例给分；否则取
        文本相似度和拼音相似度的较大者。

        拼音是本地模式一直有、外部 Provider 一直没有的能力 —— ASR 把
        "钳口" 听成 "前口" 时，文本相似度掉到很低，拼音仍然是 100。
        """
        q = query if isinstance(query, _Query) else _Query(query)
        if not q.norm:
            return 0.0

        vals = self._field_values(item)
        name, code, spec = vals.get("name", ""), vals.get("code", ""), vals.get("spec", "")

        # 精确相等：任一角色命中即满分，不再往下算
        for v in (code, name, spec):
            if v and q.norm == v:
                return 1.0
        # 「名称+规格」连读，两种顺序都认："撬具LH-815" / "LH-815撬具"
        if name and spec and q.norm in (name + spec, spec + name):
            return 1.0

        best = 0.0
        for role in ("name", "code", "spec"):
            field = vals.get(role, "")
            if not field:
                continue
            weight = self.MATCH.weights.get(role, 1.0)

            # 子串包含：按长度比例给分，避免单字 "水" 命中 "矿泉水" 拿高分
            if q.norm in field or field in q.norm:
                ratio = min(len(q.norm), len(field)) / max(len(q.norm), len(field))
                best = max(best, weight * (0.72 + 0.28 * ratio))

            # 文本相似度：partial_ratio 占比更高，容忍查询词是记录的一部分
            text_score = (fuzz.ratio(q.norm, field) * 0.4
                          + fuzz.partial_ratio(q.norm, field) * 0.6)

            # token_set_ratio 顺序无关 + 子集容忍，乘 0.95 略降权，
            # 避免压过精确子串匹配的稳定排序
            field_tokens = tokenize_for_match(item.get(self.MATCH.fields.get(role) or ""))
            if q.tokens and field_tokens:
                text_score = max(text_score, fuzz.token_set_ratio(q.tokens, field_tokens) * 0.95)

            # 拼音两路取大：ratio 认字面，token_sort_ratio 认词序颠倒。
            #
            # 但拼音分必须有文本分打底才采信 —— 拼音串都是拉丁字母、元音辅音
            # 共用，任意两个中文词的拼音都有 30~50 的基础相似度。实测
            # "矿泉水"(kuang quan shui) 对 "上钳口"(shang qian kou) 拼音 ratio
            # 高达 53，而文本相似度是 0。不设门槛的话 "查询库存" "帮我放首歌"
            # 这类跟物料无关的话都会越过 candidate_floor=0.34 返回候选列表，
            # 15 个噪声词实测误报 11 个。
            #
            # 谐音场景不受影响：ASR 把 "钳口" 听成 "前口"，字面仍有重叠
            # （实测文本分 56），门槛拦不住它。宁可漏掉字面完全不沾边的极端
            # 谐音，也不能让无关问句召回物料 —— 后者会把 LLM 带偏。
            if text_score >= _PINYIN_TEXT_GATE:
                field_pinyin = _pinyin(field)
                pinyin_score = max(fuzz.ratio(q.pinyin, field_pinyin) * 0.85,
                                   fuzz.token_sort_ratio(q.pinyin, field_pinyin) * 0.8)
                text_score = max(text_score, pinyin_score)

            best = max(best, weight * text_score / 100.0)

        return min(best, 1.0)

    def _score_indexed(self, q: "_Query", entry: "_Indexed") -> float:
        """打分的预计算版本，公式与 ``_score`` 完全一致，只是字段的归一化 /
        分词 / 拼音在拉取时就算好了。实测 10000 条从 300ms 降到 85ms。"""
        if not q.norm:
            return 0.0

        f = entry.fields
        name = f.get("name", ("", "", ""))
        code = f.get("code", ("", "", ""))
        spec = f.get("spec", ("", "", ""))

        for v in (code[0], name[0], spec[0]):
            if v and q.norm == v:
                return 1.0
        if name[0] and spec[0] and q.norm in (name[0] + spec[0], spec[0] + name[0]):
            return 1.0

        best = 0.0
        for role in ("name", "code", "spec"):
            fn, ft, fp = f.get(role, ("", "", ""))
            if not fn:
                continue
            weight = self.MATCH.weights.get(role, 1.0)

            if q.norm in fn or fn in q.norm:
                ratio = min(len(q.norm), len(fn)) / max(len(q.norm), len(fn))
                best = max(best, weight * (0.72 + 0.28 * ratio))

            text_score = (fuzz.ratio(q.norm, fn) * 0.4
                          + fuzz.partial_ratio(q.norm, fn) * 0.6)
            if q.tokens and ft:
                text_score = max(text_score, fuzz.token_set_ratio(q.tokens, ft) * 0.95)

            if text_score >= _PINYIN_TEXT_GATE:
                text_score = max(text_score,
                                 fuzz.ratio(q.pinyin, fp) * 0.85,
                                 fuzz.token_sort_ratio(q.pinyin, fp) * 0.8)

            best = max(best, weight * text_score / 100.0)

        return min(best, 1.0)

    def _uses_custom_score(self) -> bool:
        """子类是否覆盖了 ``_score``。

        覆盖了就必须走原始 dict 的慢路径 —— 预计算索引里没有子类自定义
        逻辑需要的字段，强行走快路径会静默改变它的行为。
        """
        return type(self)._score is not LocalMatchMixin._score

    def _rank(self, query: str, items: list) -> list:
        """按分降序返回 [(score, item), ...]，已滤掉低于 candidate_floor 的噪声。

        ``items`` 既可以是原始 dict 列表，也可以是 ``_Indexed`` 列表；后者
        走预计算快路径。子类覆盖了 ``_score`` 时一律走 dict 慢路径。
        """
        q = _Query(query)
        if items and isinstance(items[0], _Indexed) and not self._uses_custom_score():
            scored = [(self._score_indexed(q, e), e.raw) for e in items]
        else:
            plain = [e.raw if isinstance(e, _Indexed) else e for e in items]
            scored = [(self._score(q, it), it) for it in plain]
        scored = [x for x in scored if x[0] >= self.MATCH.candidate_floor]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    # ── 候选与响应 ──

    def _as_candidate(self, score: float, item: dict) -> dict:
        """候选项的对外结构。子类可覆盖以补充额外字段。"""
        f = self.MATCH.fields
        return {
            "name": item.get(f.get("name") or "", ""),
            "score": round(score, 3),
            "entity_type": "material",
            "extra": {
                "sku": item.get(f.get("code") or "", ""),
                "variant": item.get(f.get("spec") or "", ""),
            },
        }

    def _fmt_candidate(self, c: dict) -> str:
        extra = c.get("extra") or {}
        tail = extra.get("variant") or extra.get("sku")
        return f"{c['name']}（{tail}）" if tail else c["name"]

    def _not_found(self, query: str) -> dict:
        return {"success": False, "error": "not_found",
                "message": f"未找到备品：{query}"}

    def _ambiguous(self, query: str, candidates: list) -> dict:
        listed = "、".join(self._fmt_candidate(c) for c in candidates)
        return {
            "success": False,
            "error": "ambiguous_name",
            "candidates": candidates,
            "message": (f"'{query}' 匹配到多个备品：{listed}。"
                        "请告知具体是哪一个（可说规格或备件编号）"),
        }

    # ── 定位 ──

    # ── 缓存 ──

    CACHE_TTL: float = _DEFAULT_CACHE_TTL

    def _cache_key(self):
        """缓存隔离键。

        必须包含租户 / 仓库标识：多租户部署下不同连接绑定不同的外部仓库，
        共用一份缓存会让 A 租户看到 B 租户的物料。
        """
        cfg = getattr(self, "config", None) or {}
        return (
            type(self).__name__,
            cfg.get("api_base_url", ""),
            cfg.get("external_tenant_id", ""),
            cfg.get("external_warehouse_id", ""),
        )

    def _load_items(self, force: bool = False):
        """取全量列表，带缓存和预计算索引。

        返回 ``(indexed_list, error, from_cache)``。``force=True`` 跳过缓存。
        """
        key = self._cache_key()
        now = time.monotonic()
        entry = _item_cache.get(key)

        if not force and entry is not None and (now - entry.fetched_at) < self.CACHE_TTL:
            return entry.indexed, None, True

        items, err = self._fetch_items()
        if err:
            # 拉取失败时宁可用过期数据也不要直接失败 —— 对方 ERP 抖一下不该
            # 让用户完全查不了东西。但要留痕，否则会掩盖长时间的接口故障。
            if entry is not None:
                logger.warning(
                    "拉取全量失败，回退到 %.0fs 前的缓存: %s",
                    now - entry.fetched_at, (err or {}).get("message", err))
                return entry.indexed, None, True
            return None, err, False

        indexed = [_Indexed(it, self.MATCH.fields) for it in (items or [])]
        _item_cache.put(key, items, indexed, now, refreshed=force)
        logger.info("全量列表已刷新: %d 条 (%s)", len(indexed), type(self).__name__)
        return indexed, None, False

    def invalidate_cache(self):
        """丢弃本 Provider 的缓存。写操作改变了物料主数据时调用。"""
        _item_cache.invalidate(self._cache_key())

    def _may_force_refresh(self) -> bool:
        """距上次强制刷新是否已超过防抖间隔。"""
        entry = _item_cache.get(self._cache_key())
        if entry is None:
            return True
        return (time.monotonic() - entry.last_refresh_attempt) >= _REFRESH_DEBOUNCE

    # ── 定位 ──

    def _locate(self, query: str, fuzzy: bool = True):
        """解析查询词 → ``(item, error_response)``，二者恒有一个为 None。

        分数够高且无并列 → 命中；否则返回候选让用户澄清。``fuzzy=False``
        时只接受精确命中（分数 >= 0.999），用于出入库这类不能猜的操作。

        **实时性**：命中走缓存拿性能；一旦匹配不上，强制刷新一次再匹配。
        用户刚在 ERP 里新增的物料，第一次查询就能查到，不必等 TTL 到期 ——
        这是缓存方案里最容易出事的场景，用「未命中即刷新」正面解决。
        刷新本身有 ``_REFRESH_DEBOUNCE`` 防抖，连问几个不存在的东西不会
        把对方接口打爆。
        """
        indexed, err, from_cache = self._load_items()
        if err:
            # 拉全量失败不等于查不到 —— 有的 WMS 没有列表接口，只能靠不带过滤
            # 的查询碰运气，失败时退化成按名称/编码单点查询仍可能命中。
            # 默认不退化（直接返回错误），需要的 Provider 覆盖这个钩子。
            return self._locate_fallback(query, err)

        ranked = self._rank(query, indexed) if indexed else []

        # 缓存里找不到 → 可能是刚新增的物料，强制刷新重试一次
        if not ranked and from_cache and self._may_force_refresh():
            logger.info("缓存中未匹配到 %r，强制刷新后重试", query)
            fresh, err2, _ = self._load_items(force=True)
            if err2 is None and fresh:
                ranked = self._rank(query, fresh)

        if not ranked:
            return None, self._not_found(query)

        top_score, top = ranked[0]
        tied = [x for x in ranked if abs(x[0] - top_score) < self.MATCH.tie_epsilon]

        def _ask():
            return None, self._ambiguous(
                query, [self._as_candidate(s, it) for s, it in ranked[:6]])

        # 多个精确命中 → 必须澄清，哪怕分数满分。
        #
        # 重构前两个 Provider 都是先 `if top_score >= 0.999: return top`、
        # 并列检查在其后 —— 于是两个同名物料（都是名称精确匹配、都得 1.0）
        # 会直接返回排在前面的那个，用户根本没机会选。这与原实现自己的注释
        # 「同分并列 → 让用户澄清，不能替用户猜（写操作尤其危险）」矛盾，
        # 出库时选错物料是实打实的损失。
        #
        # 这是本次重构里唯一一处有意的行为变更。
        if top_score >= 0.999:
            return _ask() if len(tied) > 1 else (self._refresh_item(top), None)

        # 到这里说明没有精确命中。写操作不猜，直接告知查不到。
        if not fuzzy:
            return None, {"success": False, "error": "not_found",
                          "message": f"未精确匹配到备品：{query}"}

        if len(tied) > 1 or top_score < self.MATCH.confident_score:
            return _ask()

        return self._refresh_item(top), None

    def _refresh_item(self, item: dict) -> dict:
        """定位命中后取该条的实时数据。

        **缓存只能回答「是哪一条」，不能回答「这条现在有多少」。** 库存数字
        变化频繁 —— 用户刚出库 5 件，紧接着问库存，如果直接返回缓存里的记录
        就会报出库前的数字。实测中这个问题表现为 e2e 用例
        test_connection_binding_beats_provider_stored_config 在全量跑时失败：
        前一个用例入库 7 件，后一个用例从缓存读到了入库前的库存。

        默认返回原记录（对没有单条查询接口的 WMS 只能如此）。Provider 有
        按编码查单条的能力时应当覆盖此方法，用 ``MATCH.fields["code"]``
        去取最新的一条；取失败要返回原记录，不能让整个查询失败。
        """
        return item

    def _locate_fallback(self, query: str, err: dict):
        """``_fetch_items`` 失败时的退路，返回 ``(item, error_response)``。

        默认把错误原样上抛。Provider 若有单点查询接口可以覆盖此方法，
        用查询词直接问一次对方系统 —— 注意那种场景下要用**原始查询词**，
        不能用 ``norm_for_match`` 的结果，后者去掉了标点，发给对方的精确
        查询接口会匹配不上。
        """
        return None, err

    # ── 子类必须实现 ──

    def _fetch_items(self):
        """拉全量列表，返回 ``(items, error_response)``。"""
        raise NotImplementedError(
            f"{type(self).__name__} 必须实现 _fetch_items()"
        )
