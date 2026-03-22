"""
模糊匹配核心模块 - 基于 rapidfuzz + pypinyin 实现两层模糊匹配
"""
import sqlite3

from rapidfuzz import fuzz
from pypinyin import lazy_pinyin, Style


class FuzzyMatcher:
    """模糊匹配器，支持文本编辑距离和中文拼音相似度两层匹配"""

    def __init__(self, get_conn):
        """
        Args:
            get_conn: 返回数据库连接的可调用对象，每次需要时调用并在用完后关闭
        """
        self._get_conn = get_conn
        self._index: list[dict] | None = None
        self._dirty = True

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
                    "pinyin": self._get_pinyin(name),
                })
                # 索引 sku（如果 sku 与 name 不同）
                if sku and sku != name:
                    index.append({
                        "name": sku,
                        "entity_type": "material",
                        "entity_id": mid,
                        "extra": extra,
                        "pinyin": self._get_pinyin(sku),
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
                    "pinyin": self._get_pinyin(name),
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
                        "pinyin": self._get_pinyin(display_name),
                    })
                # 索引 username（如果与 display_name 不同）
                if username != display_name:
                    index.append({
                        "name": username,
                        "entity_type": "operator",
                        "entity_id": uid,
                        "extra": {},
                        "pinyin": self._get_pinyin(username),
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

    def search(self, query: str, entity_type: str = "all",
               top_k: int = 5, threshold: float = 50.0) -> list[dict]:
        """
        模糊搜索，返回 top_k 个候选。

        算法: score = max(text_similarity, pinyin_similarity * 0.9)
        过滤 score < threshold 的结果，按 score 降序排序。
        """
        self._ensure_index()

        query_pinyin = self._get_pinyin(query)
        results = []

        for entry in self._index:
            if entity_type != "all" and entry["entity_type"] != entity_type:
                continue

            text_score = fuzz.token_sort_ratio(query, entry["name"])
            pinyin_score = fuzz.token_sort_ratio(query_pinyin, entry["pinyin"]) * 0.9
            score = max(text_score, pinyin_score)

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

        置信度判定: 最高分 > 85 且与第二名差距 > 15 → confident=True
        """
        candidates = self.search(query, entity_type=entity_type, top_k=5, threshold=50.0)

        if not candidates:
            return {
                "best_match": None,
                "confident": False,
                "candidates": candidates,
            }

        best = candidates[0]
        second_score = candidates[1]["score"] if len(candidates) > 1 else 0
        gap = best["score"] - second_score
        confident = best["score"] > 85 and gap > 15

        return {
            "best_match": best,
            "confident": confident,
            "candidates": candidates,
        }
