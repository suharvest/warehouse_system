"""
模糊匹配核心模块 - 基于 rapidfuzz + pypinyin 实现两层模糊匹配
"""
import re
import sqlite3

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


class FuzzyMatcher:
    """模糊匹配器，支持文本编辑距离和中文拼音相似度两层匹配"""

    def __init__(self, get_conn, *, confident_score: float = 80.0,
                 confident_gap: float = 10.0):
        """
        Args:
            get_conn: 返回数据库连接的可调用对象，每次需要时调用并在用完后关闭
            confident_score: 最高分超过此值才可能判为 confident
            confident_gap: 最高分与第二名差距超过此值才判为 confident
        """
        self._get_conn = get_conn
        self._confident_score = confident_score
        self._confident_gap = confident_gap
        self._index: list[dict] | None = None
        self._dirty = True

    @staticmethod
    def _normalize(text: str) -> str:
        """去除空格、横杠、斜杠、括号、逗号等干扰字符，统一比较基准"""
        return re.sub(r'[\s\-－/／\(\)（）\[\]【】,，、]+', '', text).lower()

    def _get_pinyin(self, text: str) -> str:
        """将文本转为无声调拼音字符串"""
        return ' '.join(lazy_pinyin(text, style=Style.NORMAL))

    def _build_index(self):
        """从数据库加载所有可搜索实体名称，构建索引（含拼音 + tenant_id/warehouse_id 用于隔离过滤）

        Phase 2a: reads via SQLAlchemy Core engine. The injected ``get_conn``
        is retained for backward compatibility (and used by other read methods
        on this class) but is unused here — SA reads share the same DB.
        """
        index = []
        engine = get_engine()
        with engine.connect() as conn:
            # 索引 materials: name 和 sku 都作为可搜索名称
            stmt = select(
                materials.c.id, materials.c.name, materials.c.sku,
                materials.c.category, materials.c.tenant_id, materials.c.warehouse_id,
            ).where(materials.c.is_disabled == 0)
            for row in conn.execute(stmt).fetchall():
                mid, name, sku, category = row.id, row.name, row.sku, row.category
                tid, whid = row.tenant_id, row.warehouse_id
                extra = {"sku": sku, "category": category}
                index.append({
                    "name": name,
                    "entity_type": "material",
                    "entity_id": mid,
                    "tenant_id": tid,
                    "warehouse_id": whid,
                    "extra": extra,
                    "pinyin": self._get_pinyin(self._normalize(name)),
                })
                if sku and sku != name:
                    index.append({
                        "name": sku,
                        "entity_type": "material",
                        "entity_id": mid,
                        "tenant_id": tid,
                        "warehouse_id": whid,
                        "extra": extra,
                        "pinyin": self._get_pinyin(self._normalize(sku)),
                    })

            # 索引 "name + variant" 组合，让 "七彩灯A" 能直接匹配
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
                index.append({
                    "name": combined,
                    "entity_type": "material",
                    "entity_id": mid,
                    "tenant_id": tid,
                    "warehouse_id": whid,
                    "extra": {"sku": sku, "category": category, "variant": variant},
                    "pinyin": self._get_pinyin(self._normalize(combined)),
                })

            # 索引 contacts: name
            stmt = select(
                contacts.c.id, contacts.c.name, contacts.c.is_supplier,
                contacts.c.is_customer, contacts.c.tenant_id, contacts.c.warehouse_id,
            ).where(contacts.c.is_disabled == 0)
            for row in conn.execute(stmt).fetchall():
                cid, name = row.id, row.name
                index.append({
                    "name": name,
                    "entity_type": "contact",
                    "entity_id": cid,
                    "tenant_id": row.tenant_id,
                    "warehouse_id": row.warehouse_id,
                    "extra": {"is_supplier": bool(row.is_supplier), "is_customer": bool(row.is_customer)},
                    "pinyin": self._get_pinyin(self._normalize(name)),
                })

            # 索引 users: display_name 和 username（仅 operate/admin 且未禁用）
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
                    index.append({
                        "name": display_name,
                        "entity_type": "operator",
                        "entity_id": uid,
                        "tenant_id": tid,
                        "warehouse_id": None,
                        "extra": {},
                        "pinyin": self._get_pinyin(self._normalize(display_name)),
                    })
                if username != display_name:
                    index.append({
                        "name": username,
                        "entity_type": "operator",
                        "entity_id": uid,
                        "tenant_id": tid,
                        "warehouse_id": None,
                        "extra": {},
                        "pinyin": self._get_pinyin(self._normalize(username)),
                    })

        self._index = index

    def _ensure_index(self):
        if self._dirty or self._index is None:
            self._build_index()
            self._dirty = False

    def invalidate_cache(self):
        """写操作后调用以使缓存失效"""
        self._dirty = True

    def _judge_confident(self, candidates: list[dict]) -> bool:
        """根据已排序（降序）候选列表判定置信度。

        规则:
        - 空 → False
        - 并列第一 → False
        - 单候选 → score ≥ 75
        - score ≥ 95 → True
        - score ≥ 90 且 gap > 5 → True
        - 否则 → score ≥ _confident_score 且 gap > _confident_gap
        """
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
                    norm_name: str, name_pinyin: str) -> float:
        """计算综合匹配分数。

        策略:
        1. 文本包含（query⊂name 或 name⊂query）→ 95 分（最强信号）
        2. 文本相似度: ratio * 0.4 + partial_ratio * 0.6（partial 容易虚高，降权混合）
        3. 拼音相似度: max(ratio * 0.85, token_sort_ratio * 0.8)
        取 2 和 3 的较大值。
        """
        # 文本包含检查
        if norm_query in norm_name:
            # query ⊂ name（如 "轴承6309" ⊂ "轴承6309AEMC3"）→ 强信号 90-100
            return 90.0 + 10.0 * (len(norm_query) / len(norm_name))
        if norm_name in norm_query:
            # name ⊂ query（如 "轴承" ⊂ "轴承22062RS"）→ 弱信号，按长度比降权
            ratio = len(norm_name) / len(norm_query)
            return 50.0 + 30.0 * ratio  # 50-80 分区间

        # 文本层
        text_ratio = fuzz.ratio(norm_query, norm_name)
        text_partial = fuzz.partial_ratio(norm_query, norm_name)
        text_score = text_ratio * 0.4 + text_partial * 0.6

        # 拼音层
        pinyin_ratio = fuzz.ratio(query_pinyin, name_pinyin) * 0.85
        pinyin_token = fuzz.token_sort_ratio(query_pinyin, name_pinyin) * 0.8
        pinyin_score = max(pinyin_ratio, pinyin_token)

        return max(text_score, pinyin_score)

    def search(self, query: str, entity_type: str = "all",
               top_k: int = 5, threshold: float = 50.0,
               tenant_id: int | None = None,
               warehouse_id: int | None = None) -> list[dict]:
        """
        模糊搜索，返回 top_k 个候选。

        算法: 文本包含 > 文本相似度(ratio+partial混合) > 拼音相似度(ratio+token_sort)
        过滤 score < threshold 的结果，按 score 降序排序。

        scope 过滤:
        - tenant_id 非 None：只返回该租户的实体（operator 类除外，跨租户全局可见暂不限制）
        - tenant_id 为 None：不限定（全局 admin / 历史调用方）
        - warehouse_id 非 None：进一步限定到该仓库
        """
        self._ensure_index()

        norm_query = self._normalize(query)
        if not norm_query:
            return []
        query_pinyin = self._get_pinyin(norm_query)
        results = []

        for entry in self._index:
            if entity_type != "all" and entry["entity_type"] != entity_type:
                continue

            # 租户隔离：调用方提供 tenant_id 时强制过滤
            if tenant_id is not None:
                entry_tid = entry.get("tenant_id")
                if entry_tid is not None and entry_tid != tenant_id:
                    continue
            if warehouse_id is not None:
                entry_whid = entry.get("warehouse_id")
                # operator/contact 为租户级（warehouse_id=None），不参与仓库过滤
                if entry_whid is not None and entry_whid != warehouse_id:
                    continue

            norm_name = self._normalize(entry["name"])
            name_pinyin = entry["pinyin"]
            score = self._calc_score(norm_query, query_pinyin, norm_name, name_pinyin)

            if score >= threshold:
                results.append({
                    "name": entry["name"],
                    "score": round(score, 1),
                    "entity_type": entry["entity_type"],
                    "entity_id": entry["entity_id"],
                    "extra": entry["extra"],
                })

        # 按 score 降序排序，取 top_k
        results.sort(key=lambda x: x["score"], reverse=True)

        # 去重：同一 entity_id + entity_type 只保留最高分
        seen = set()
        deduped = []
        for r in results:
            key = (r["entity_type"], r["entity_id"])
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        # 过滤掉与第一名差距过大的候选（超过 20 分视为噪音）
        if deduped:
            top_score = deduped[0]["score"]
            deduped = [r for r in deduped if top_score - r["score"] <= 20]

        return deduped[:top_k]

    def resolve(self, query: str, entity_type: str = "all",
                tenant_id: int | None = None,
                warehouse_id: int | None = None) -> dict:
        """
        解析模糊文本为最佳匹配。

        置信度判定: 最高分 > confident_score 且与第二名差距 > confident_gap → confident=True
        scope 参数同 search()。
        """
        candidates = self.search(
            query, entity_type=entity_type, top_k=5, threshold=50.0,
            tenant_id=tenant_id, warehouse_id=warehouse_id,
        )

        if not candidates:
            return {
                "best_match": None,
                "confident": False,
                "candidates": candidates,
            }

        best = candidates[0]
        confident = self._judge_confident(candidates)

        return {
            "best_match": best,
            "confident": confident,
            "candidates": candidates,
        }

    def resolve_location_in_scope(self, material_id: int, warehouse_id: int,
                                   query: str) -> dict:
        """按产品+仓库作用域对 location 做模糊匹配。

        候选集现场查 SQL（该物料该仓库所有未耗尽批次的 DISTINCT location），
        通常 < 20 条。不走全局索引，避免跨产品污染。

        返回: {best_match, confident, candidates} 同 resolve。
        """
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
