"""
模糊匹配核心模块 - 基于 rapidfuzz + pypinyin 实现两层模糊匹配

R5: 增量失效 + 线程安全
- _index 按 entity_type 分区（material / contact / operator）
- invalidate_cache 支持 entity_type / tenant_id / warehouse_id / entity_id 粒度
- 所有索引读写经 RLock 保护（FastAPI 同步路由跑在线程池上）
- lazy_pinyin 结果缓存，按文本键去重，LRU 容量上限避免无界增长
"""
import re
import threading
from collections import OrderedDict

from rapidfuzz import fuzz
from pypinyin import lazy_pinyin, Style
from sqlalchemy import select, and_

from db import get_engine
from metadata import materials, contacts, users, batches

# R3: wire-format string enum. Tolerate both bare and package import styles.
try:
    from models import RoleName  # type: ignore
except ImportError:  # pragma: no cover
    from backend.models import RoleName  # type: ignore


# 已知 entity_type，按需构建。新增类型时在此扩展。
_ENTITY_TYPES = ("material", "contact", "operator")

# 拼音缓存上限。超过后用 LRU 顺序淘汰。
_PINYIN_CACHE_MAX = 10000


class FuzzyMatcher:
    """模糊匹配器，支持文本编辑距离和中文拼音相似度两层匹配"""

    def __init__(self, get_conn, *, confident_score: float = 80.0,
                 confident_gap: float = 10.0):
        self._get_conn = get_conn
        self._confident_score = confident_score
        self._confident_gap = confident_gap

        # 分区索引：{entity_type: list[entry_dict]}
        self._partitions: dict[str, list[dict]] = {}
        # 脏分区集合，下次 search 时按需重建
        self._dirty_partitions: set[str] = set(_ENTITY_TYPES)
        # 反向索引：(entity_type, entity_id) -> list[entry] 引用
        # 便于按 id 精准移除单行而不重建整个分区
        self._by_entity: dict[tuple[str, int], list[dict]] = {}

        # 拼音缓存：raw_text -> pinyin_str（LRU）
        self._pinyin_cache: OrderedDict[str, str] = OrderedDict()

        # 可重入锁：search 调用 _ensure_index → _build_partition → 仍持有锁
        self._lock = threading.RLock()

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _normalize(text: str) -> str:
        """去除空格、横杠、斜杠、括号、逗号等干扰字符，统一比较基准"""
        return re.sub(r'[\s\-－/／\(\)（）\[\]【】,，、]+', '', text).lower()

    @staticmethod
    def _tokenize(text: str) -> str:
        """中英文边界补空格 + 折叠空格，供 token_set_ratio 切词使用。

        关键场景：ASR / 用户口语 "银色M3螺丝" 没有空格，RapidFuzz 无法切分，
        token_set_ratio 退化成单 token。补空格后变 "银色 m3 螺丝"，与索引项
        "M3 螺丝 银色 8mm" → "m3 螺丝 银色 8mm" 的 token_set_ratio = 100，
        匹配上 "name + variant" 组合索引项。
        """
        # 中文↔ASCII 边界插空格（中→ASCII，ASCII→中）
        t = re.sub(r'([一-鿿])([A-Za-z0-9])', r'\1 \2', text)
        t = re.sub(r'([A-Za-z0-9])([一-鿿])', r'\1 \2', t)
        # 标点统一替换为空格（保留单词边界，与 _normalize 不同）
        t = re.sub(r'[\-－/／\(\)（）\[\]【】,，、]+', ' ', t)
        # 折叠多空格
        t = re.sub(r'\s+', ' ', t).strip().lower()
        return t

    def _get_pinyin(self, text: str) -> str:
        """将文本转为无声调拼音字符串（带 LRU 缓存）"""
        cache = self._pinyin_cache
        with self._lock:
            cached = cache.get(text)
            if cached is not None:
                cache.move_to_end(text)
                return cached
        # 计算放在锁外（避免阻塞读路径）
        result = ' '.join(lazy_pinyin(text, style=Style.NORMAL))
        with self._lock:
            cache[text] = result
            cache.move_to_end(text)
            while len(cache) > _PINYIN_CACHE_MAX:
                cache.popitem(last=False)
        return result

    # ---- index construction --------------------------------------------

    def _add_entry(self, entity_type: str, entity_id: int, entry: dict,
                   bucket: list[dict]):
        bucket.append(entry)
        self._by_entity.setdefault((entity_type, entity_id), []).append(entry)

    def _build_partition(self, entity_type: str) -> list[dict]:
        """重建单个分区。调用方负责加锁。"""
        bucket: list[dict] = []
        engine = get_engine()
        with engine.connect() as conn:
            if entity_type == "material":
                stmt = select(
                    materials.c.id, materials.c.name, materials.c.sku,
                    materials.c.category, materials.c.tenant_id, materials.c.warehouse_id,
                ).where(materials.c.is_disabled == 0)
                for row in conn.execute(stmt).fetchall():
                    mid, name, sku, category = row.id, row.name, row.sku, row.category
                    tid, whid = row.tenant_id, row.warehouse_id
                    extra = {"sku": sku, "category": category, "canonical_name": name}
                    self._add_entry("material", mid, {
                        "name": name, "entity_type": "material", "entity_id": mid,
                        "tenant_id": tid, "warehouse_id": whid, "extra": extra,
                        "tokens": self._tokenize(name),
                        "pinyin": self._get_pinyin(self._normalize(name)),
                    }, bucket)
                    if sku and sku != name:
                        self._add_entry("material", mid, {
                            "name": sku, "entity_type": "material", "entity_id": mid,
                            "tenant_id": tid, "warehouse_id": whid, "extra": extra,
                            "tokens": self._tokenize(sku),
                            "pinyin": self._get_pinyin(self._normalize(sku)),
                        }, bucket)
                        combined = f"{sku} {name}"
                        self._add_entry("material", mid, {
                            "name": combined, "entity_type": "material", "entity_id": mid,
                            "tenant_id": tid, "warehouse_id": whid,
                            "extra": extra,
                            "tokens": self._tokenize(combined),
                            "pinyin": self._get_pinyin(self._normalize(combined)),
                        }, bucket)
                        combined_rev = f"{name} {sku}"
                        self._add_entry("material", mid, {
                            "name": combined_rev, "entity_type": "material", "entity_id": mid,
                            "tenant_id": tid, "warehouse_id": whid,
                            "extra": extra,
                            "tokens": self._tokenize(combined_rev),
                            "pinyin": self._get_pinyin(self._normalize(combined_rev)),
                        }, bucket)

                # 索引 "name + variant" 组合
                stmt = select(
                    materials.c.id, materials.c.name, materials.c.sku,
                    materials.c.category, materials.c.tenant_id, materials.c.warehouse_id,
                    batches.c.variant,
                ).select_from(
                    batches.join(materials, batches.c.material_id == materials.c.id)
                ).where(
                    and_(
                        materials.c.is_disabled == 0,
                        batches.c.variant.isnot(None),
                        batches.c.variant != "",
                    )
                ).distinct()
                for row in conn.execute(stmt).fetchall():
                    mid, name, sku, category, tid, whid, variant = (
                        row.id, row.name, row.sku, row.category,
                        row.tenant_id, row.warehouse_id, row.variant,
                    )
                    combined = f"{name} {variant}"
                    self._add_entry("material", mid, {
                        "name": combined, "entity_type": "material", "entity_id": mid,
                        "tenant_id": tid, "warehouse_id": whid,
                        "extra": {
                            "sku": sku, "category": category,
                            "canonical_name": name, "variant": variant,
                        },
                        "tokens": self._tokenize(combined),
                        "pinyin": self._get_pinyin(self._normalize(combined)),
                    }, bucket)

            elif entity_type == "contact":
                stmt = select(
                    contacts.c.id, contacts.c.name, contacts.c.is_supplier,
                    contacts.c.is_customer, contacts.c.tenant_id, contacts.c.warehouse_id,
                ).where(contacts.c.is_disabled == 0)
                for row in conn.execute(stmt).fetchall():
                    cid, name = row.id, row.name
                    self._add_entry("contact", cid, {
                        "name": name, "entity_type": "contact", "entity_id": cid,
                        "tenant_id": row.tenant_id, "warehouse_id": row.warehouse_id,
                        "extra": {"is_supplier": bool(row.is_supplier),
                                  "is_customer": bool(row.is_customer)},
                        "tokens": self._tokenize(name),
                        "pinyin": self._get_pinyin(self._normalize(name)),
                    }, bucket)

            elif entity_type == "operator":
                stmt = select(
                    users.c.id, users.c.username, users.c.display_name, users.c.tenant_id,
                ).where(
                    and_(
                        users.c.is_disabled == 0,
                        users.c.role.in_((RoleName.OPERATE.value, RoleName.ADMIN.value)),
                    )
                )
                for row in conn.execute(stmt).fetchall():
                    uid, username, display_name = row.id, row.username, row.display_name
                    tid = row.tenant_id
                    if display_name:
                        self._add_entry("operator", uid, {
                            "name": display_name, "entity_type": "operator", "entity_id": uid,
                            "tenant_id": tid, "warehouse_id": None, "extra": {},
                            "tokens": self._tokenize(display_name),
                            "pinyin": self._get_pinyin(self._normalize(display_name)),
                        }, bucket)
                    if username != display_name:
                        self._add_entry("operator", uid, {
                            "name": username, "entity_type": "operator", "entity_id": uid,
                            "tenant_id": tid, "warehouse_id": None, "extra": {},
                            "tokens": self._tokenize(username),
                            "pinyin": self._get_pinyin(self._normalize(username)),
                        }, bucket)

        return bucket

    def _ensure_index(self):
        """按需重建脏分区。"""
        with self._lock:
            if not self._dirty_partitions:
                return
            dirty = list(self._dirty_partitions)
            for et in dirty:
                # 清掉该分区在反向索引里的引用
                old = self._partitions.get(et, [])
                if old:
                    seen_ids = {(e["entity_type"], e["entity_id"]) for e in old}
                    for key in seen_ids:
                        self._by_entity.pop(key, None)
                self._partitions[et] = self._build_partition(et)
                self._dirty_partitions.discard(et)

    # ---- public invalidation -------------------------------------------

    def invalidate_cache(self, entity_type: str | None = None,
                         tenant_id: int | None = None,
                         warehouse_id: int | None = None,
                         entity_id: int | None = None):
        """写操作后调用以使缓存失效。

        粒度（从粗到细）：
        - 全 None：完整失效（向后兼容）
        - entity_type only：只失效该类型分区
        - entity_type + tenant_id [+ warehouse_id]：只丢该 scope 下的条目，下次重建时整分区刷新
        - entity_type + entity_id：精准移除单实体的所有索引条目，分区不重建
        """
        with self._lock:
            if entity_type is None:
                # 全失效
                self._dirty_partitions = set(_ENTITY_TYPES)
                return

            if entity_type not in _ENTITY_TYPES:
                # 未知类型：保守做完整失效
                self._dirty_partitions = set(_ENTITY_TYPES)
                return

            if entity_id is not None:
                # 精准单行移除：从分区和反向索引里同时清理
                key = (entity_type, entity_id)
                entries = self._by_entity.pop(key, None)
                if entries:
                    bucket = self._partitions.get(entity_type)
                    if bucket is not None:
                        ent_set = {id(e) for e in entries}
                        self._partitions[entity_type] = [
                            e for e in bucket if id(e) not in ent_set
                        ]
                # 不标 dirty：单行已剔除，无需重建
                return

            # entity_type [+tenant_id +warehouse_id]：标分区为脏
            # tenant_id / warehouse_id 目前不做更细粒度的部分清理 —— 直接整分区重建。
            # 这仍比"重建所有分区"省 2/3 的功夫，且实现简单。
            self._dirty_partitions.add(entity_type)

    # ---- scoring -------------------------------------------------------

    def _judge_confident(self, candidates: list[dict]) -> bool:
        if not candidates:
            return False
        best = candidates[0]
        if len(candidates) >= 2 and best["score"] == candidates[1]["score"]:
            return False
        if len(candidates) == 1:
            return best["score"] >= 75.0
        if best["score"] >= 95.0:
            return True
        gap = best["score"] - candidates[1]["score"]
        if best["score"] >= 90.0:
            return gap > 5.0
        return best["score"] >= self._confident_score and gap > self._confident_gap

    def _calc_score(self, norm_query: str, query_pinyin: str,
                    norm_name: str, name_pinyin: str,
                    query_tokens: str | None = None,
                    name_tokens: str | None = None) -> float:
        if norm_query in norm_name:
            return 90.0 + 10.0 * (len(norm_query) / len(norm_name))
        if norm_name in norm_query:
            ratio = len(norm_name) / len(norm_query)
            return 50.0 + 30.0 * ratio

        text_ratio = fuzz.ratio(norm_query, norm_name)
        text_partial = fuzz.partial_ratio(norm_query, norm_name)
        text_score = text_ratio * 0.4 + text_partial * 0.6

        # token_set_ratio：顺序无关 + 子集容忍。
        # 处理 "银色 M3 螺丝" vs "M3 螺丝 银色 8mm" 这种口语顺序与索引顺序不一致的情况。
        # 乘 0.95 略加权降，避免压过精确子串匹配（90+）的稳定排序。
        if query_tokens and name_tokens:
            text_token_set = fuzz.token_set_ratio(query_tokens, name_tokens) * 0.95
            text_score = max(text_score, text_token_set)

        pinyin_ratio = fuzz.ratio(query_pinyin, name_pinyin) * 0.85
        pinyin_token = fuzz.token_sort_ratio(query_pinyin, name_pinyin) * 0.8
        pinyin_score = max(pinyin_ratio, pinyin_token)

        return max(text_score, pinyin_score)

    @staticmethod
    def _sku_tokens(norm_query: str) -> list[str]:
        """Extract code-like tokens from a normalized mixed-language query."""
        return re.findall(r'[a-z]+\d+|\d+', norm_query)

    def _sku_name_score(self, norm_query: str, entry: dict) -> float | None:
        """Boost material matches when SKU/code and name both appear in any order.

        This covers spoken forms such as "SKU为LV0045的电极帽" and "电极帽LB0045".
        The SKU token may be slightly off (e.g. V heard as B), but the material
        name must also be present to avoid broad code-only false positives.
        """
        extra = entry.get("extra") or {}
        sku = extra.get("sku")
        canonical_name = extra.get("canonical_name")
        if not sku or not canonical_name:
            return None

        norm_sku = self._normalize(sku)
        norm_canonical = self._normalize(canonical_name)
        if not norm_sku or not norm_canonical or norm_canonical not in norm_query:
            return None

        best_token_score = 0.0
        sku_digits = ''.join(re.findall(r'\d+', norm_sku))
        for token in self._sku_tokens(norm_query):
            token_score = fuzz.ratio(token, norm_sku)
            token_digits = ''.join(re.findall(r'\d+', token))
            if sku_digits and token_digits == sku_digits:
                token_score = max(token_score, 92.0)
            best_token_score = max(best_token_score, token_score)

        if best_token_score >= 90.0:
            return 96.0 + min((best_token_score - 90.0) / 10.0, 1.0)
        if best_token_score >= 80.0:
            return 90.0 + (best_token_score - 80.0) * 0.4
        return None

    # ---- public search -------------------------------------------------

    def search(self, query: str, entity_type: str = "all",
               top_k: int = 5, threshold: float = 50.0,
               tenant_id: int | None = None,
               warehouse_id: int | None = None) -> list[dict]:
        self._ensure_index()

        norm_query = self._normalize(query)
        if not norm_query:
            return []
        query_pinyin = self._get_pinyin(norm_query)
        # 用原始 query（保留中英文边界信息）做 tokenize，给 token_set_ratio 用
        query_tokens = self._tokenize(query)
        results = []

        # 在锁内拍快照，避免并发写入时迭代损坏
        with self._lock:
            if entity_type == "all":
                buckets = list(self._partitions.values())
            else:
                buckets = [self._partitions.get(entity_type, [])]
            # snapshot：复制成单一 list 以便锁外迭代
            snapshot = [e for b in buckets for e in b]

        for entry in snapshot:
            if entity_type != "all" and entry["entity_type"] != entity_type:
                continue
            if tenant_id is not None:
                entry_tid = entry.get("tenant_id")
                if entry_tid is not None and entry_tid != tenant_id:
                    continue
            if warehouse_id is not None:
                entry_whid = entry.get("warehouse_id")
                if entry_whid is not None and entry_whid != warehouse_id:
                    continue

            norm_name = self._normalize(entry["name"])
            name_pinyin = entry["pinyin"]
            name_tokens = entry.get("tokens")
            score = self._calc_score(norm_query, query_pinyin, norm_name, name_pinyin,
                                     query_tokens=query_tokens, name_tokens=name_tokens)
            sku_name_score = self._sku_name_score(norm_query, entry)
            if sku_name_score is not None:
                score = max(score, sku_name_score)

            if score >= threshold:
                results.append({
                    "name": entry["name"],
                    "score": round(score, 1),
                    "entity_type": entry["entity_type"],
                    "entity_id": entry["entity_id"],
                    "extra": entry["extra"],
                })

        results.sort(key=lambda x: x["score"], reverse=True)

        seen = set()
        deduped = []
        for r in results:
            key = (r["entity_type"], r["entity_id"])
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        if deduped:
            top_score = deduped[0]["score"]
            deduped = [r for r in deduped if top_score - r["score"] <= 20]

        return deduped[:top_k]

    def resolve(self, query: str, entity_type: str = "all",
                tenant_id: int | None = None,
                warehouse_id: int | None = None) -> dict:
        candidates = self.search(
            query, entity_type=entity_type, top_k=5, threshold=50.0,
            tenant_id=tenant_id, warehouse_id=warehouse_id,
        )

        if not candidates:
            return {"best_match": None, "confident": False, "candidates": candidates}

        best = candidates[0]
        confident = self._judge_confident(candidates)
        return {"best_match": best, "confident": confident, "candidates": candidates}

    def resolve_location_in_scope(self, material_id: int, warehouse_id: int,
                                   query: str) -> dict:
        """按产品+仓库作用域对 location 做模糊匹配（绕过全局索引）。"""
        if not query:
            return {"best_match": None, "confident": False, "candidates": []}

        engine = get_engine()
        stmt = select(batches.c.location).where(
            and_(
                batches.c.material_id == material_id,
                batches.c.warehouse_id == warehouse_id,
                batches.c.is_exhausted == 0,
                batches.c.quantity > 0,
                batches.c.location.isnot(None),
                batches.c.location != "",
            )
        ).distinct()
        with engine.connect() as conn:
            locations = [r.location for r in conn.execute(stmt).fetchall()]

        if not locations:
            return {"best_match": None, "confident": False, "candidates": []}

        norm_query = self._normalize(query)
        if not norm_query:
            return {"best_match": None, "confident": False, "candidates": []}
        query_pinyin = self._get_pinyin(norm_query)

        scored = []
        for loc in locations:
            norm_loc = self._normalize(loc)
            loc_pinyin = self._get_pinyin(norm_loc)
            score = self._calc_score(norm_query, query_pinyin, norm_loc, loc_pinyin)
            if score >= 50.0:
                scored.append({
                    "name": loc,
                    "score": round(score, 1),
                    "entity_type": "location",
                    "entity_id": None,
                    "extra": {},
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        if scored:
            top_score = scored[0]["score"]
            scored = [r for r in scored if top_score - r["score"] <= 20]

        if not scored:
            return {"best_match": None, "confident": False, "candidates": []}

        best = scored[0]
        confident = self._judge_confident(scored)
        return {"best_match": best, "confident": confident, "candidates": scored[:5]}
