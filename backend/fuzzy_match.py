"""
模糊匹配核心模块 - 基于 rapidfuzz + pypinyin 实现两层模糊匹配
"""
import re
import sqlite3

from rapidfuzz import fuzz
from pypinyin import lazy_pinyin, Style


class FuzzyMatcher:
    """模糊匹配器，支持文本编辑距离和中文拼音相似度两层匹配"""

    def __init__(self, get_conn, *, confident_score: float = 85.0,
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
        return re.sub(r'[\s\-－/／\(\)（）\[\]【】,，、]+', '', text)

    def _get_pinyin(self, text: str) -> str:
        """将文本转为无声调拼音字符串"""
        return ' '.join(lazy_pinyin(text, style=Style.NORMAL))

    def _build_index(self):
        """从数据库加载所有可搜索实体名称，构建索引（含拼音）"""
        index = []
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 索引 materials: name 和 sku 都作为可搜索名称
            cursor.execute(
                "SELECT id, name, sku, category FROM materials WHERE is_disabled = 0"
            )
            for row in cursor.fetchall():
                mid, name, sku, category = row['id'], row['name'], row['sku'], row['category']
                extra = {"sku": sku, "category": category}
                # 索引 name
                index.append({
                    "name": name,
                    "entity_type": "material",
                    "entity_id": mid,
                    "extra": extra,
                    "pinyin": self._get_pinyin(self._normalize(name)),
                })
                # 索引 sku（如果 sku 与 name 不同）
                if sku and sku != name:
                    index.append({
                        "name": sku,
                        "entity_type": "material",
                        "entity_id": mid,
                        "extra": extra,
                        "pinyin": self._get_pinyin(self._normalize(sku)),
                    })

            # 索引 contacts: name
            cursor.execute(
                "SELECT id, name, is_supplier, is_customer FROM contacts WHERE is_disabled = 0"
            )
            for row in cursor.fetchall():
                cid, name = row['id'], row['name']
                index.append({
                    "name": name,
                    "entity_type": "contact",
                    "entity_id": cid,
                    "extra": {"is_supplier": bool(row['is_supplier']), "is_customer": bool(row['is_customer'])},
                    "pinyin": self._get_pinyin(self._normalize(name)),
                })

            # 索引 users: display_name 和 username（仅 operate/admin 且未禁用）
            cursor.execute(
                "SELECT id, username, display_name FROM users "
                "WHERE is_disabled = 0 AND role IN ('operate', 'admin')"
            )
            for row in cursor.fetchall():
                uid, username, display_name = row['id'], row['username'], row['display_name']
                # 索引 display_name（如果有）
                if display_name:
                    index.append({
                        "name": display_name,
                        "entity_type": "operator",
                        "entity_id": uid,
                        "extra": {},
                        "pinyin": self._get_pinyin(self._normalize(display_name)),
                    })
                # 索引 username（如果与 display_name 不同）
                if username != display_name:
                    index.append({
                        "name": username,
                        "entity_type": "operator",
                        "entity_id": uid,
                        "extra": {},
                        "pinyin": self._get_pinyin(self._normalize(username)),
                    })
        finally:
            conn.close()

        self._index = index

    def _ensure_index(self):
        if self._dirty or self._index is None:
            self._build_index()
            self._dirty = False

    def invalidate_cache(self):
        """写操作后调用以使缓存失效"""
        self._dirty = True

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
               top_k: int = 5, threshold: float = 50.0) -> list[dict]:
        """
        模糊搜索，返回 top_k 个候选。

        算法: 文本包含 > 文本相似度(ratio+partial混合) > 拼音相似度(ratio+token_sort)
        过滤 score < threshold 的结果，按 score 降序排序。
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

        return deduped[:top_k]

    def resolve(self, query: str, entity_type: str = "all") -> dict:
        """
        解析模糊文本为最佳匹配。

        置信度判定: 最高分 > confident_score 且与第二名差距 > confident_gap → confident=True
        """
        candidates = self.search(query, entity_type=entity_type, top_k=5, threshold=50.0)

        if not candidates:
            return {
                "best_match": None,
                "confident": False,
                "candidates": candidates,
            }

        best = candidates[0]
        if len(candidates) == 1:
            # 唯一候选，无歧义，但仍需足够相似
            confident = best["score"] >= 75.0
        elif best["score"] >= 95.0:
            # 强匹配（近似完全包含），直接确认，不被短子串干扰
            confident = True
        else:
            second_score = candidates[1]["score"]
            gap = best["score"] - second_score
            # 梯度 gap：score 越高要求的差距越小
            if best["score"] >= 90.0:
                confident = gap > 5.0
            else:
                confident = best["score"] > self._confident_score and gap > self._confident_gap

        return {
            "best_match": best,
            "confident": confident,
            "candidates": candidates,
        }
