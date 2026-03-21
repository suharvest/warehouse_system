"""
模糊匹配模块 - 使用 rapidfuzz + pypinyin 实现两层模糊匹配
"""
import sqlite3
from rapidfuzz import fuzz
from pypinyin import lazy_pinyin, Style


class FuzzyMatcher:
    """模糊匹配器，支持文本编辑距离和中文拼音相似度"""

    def __init__(self, get_conn):
        """
        Args:
            get_conn: 返回数据库连接的可调用对象，每次需要时调用并在用完后关闭
        """
        self._get_conn = get_conn
        self._cache = None

    def _load_entities(self) -> list[dict]:
        """从数据库加载所有可匹配的实体"""
        if self._cache is not None:
            return self._cache

        conn = self._get_conn()
        try:
            entities = []
            cursor = conn.cursor()

            # materials: name + sku
            cursor.execute(
                'SELECT id, name, sku, category FROM materials WHERE is_disabled = 0'
            )
            for row in cursor.fetchall():
                entities.append({
                    'name': row['name'],
                    'entity_type': 'material',
                    'entity_id': row['id'],
                    'extra': {'sku': row['sku'], 'category': row['category']},
                })

            # contacts: name
            cursor.execute(
                'SELECT id, name, is_supplier, is_customer FROM contacts WHERE is_disabled = 0'
            )
            for row in cursor.fetchall():
                entities.append({
                    'name': row['name'],
                    'entity_type': 'contact',
                    'entity_id': row['id'],
                    'extra': {
                        'is_supplier': bool(row['is_supplier']),
                        'is_customer': bool(row['is_customer']),
                    },
                })

            # operators: display_name + username
            cursor.execute(
                'SELECT id, username, display_name FROM users WHERE is_disabled = 0'
            )
            for row in cursor.fetchall():
                name = row['display_name'] or row['username']
                entities.append({
                    'name': name,
                    'entity_type': 'operator',
                    'entity_id': row['id'],
                    'extra': None,
                })

            self._cache = entities
            return entities
        finally:
            conn.close()

    @staticmethod
    def _get_pinyin(text: str) -> str:
        """获取文本的拼音（不带声调）"""
        return ''.join(lazy_pinyin(text, style=Style.NORMAL))

    @staticmethod
    def _compute_score(query: str, candidate: str) -> float:
        """计算综合匹配分数: max(text_similarity, pinyin_similarity * 0.9)"""
        # 文本编辑距离相似度
        text_score = fuzz.token_sort_ratio(query.lower(), candidate.lower())

        # 拼音相似度
        query_pinyin = FuzzyMatcher._get_pinyin(query)
        candidate_pinyin = FuzzyMatcher._get_pinyin(candidate)
        pinyin_score = fuzz.token_sort_ratio(query_pinyin, candidate_pinyin) * 0.9

        return max(text_score, pinyin_score)

    def search(
        self,
        query: str,
        entity_type: str = "all",
        top_k: int = 5,
        threshold: float = 50.0,
    ) -> list[dict]:
        """搜索匹配的实体

        Args:
            query: 搜索文本
            entity_type: 实体类型过滤 ("all", "material", "contact", "operator")
            top_k: 返回前 k 个结果
            threshold: 最低分数阈值

        Returns:
            匹配结果列表，按分数降序排列
        """
        entities = self._load_entities()

        results = []
        for entity in entities:
            if entity_type != "all" and entity['entity_type'] != entity_type:
                continue

            score = self._compute_score(query, entity['name'])
            if score >= threshold:
                results.append({
                    'name': entity['name'],
                    'score': round(score, 1),
                    'entity_type': entity['entity_type'],
                    'entity_id': entity['entity_id'],
                    'extra': entity['extra'],
                })

        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]

    def resolve(self, query: str, entity_type: str = "all") -> dict:
        """解析名称，判断是否有足够置信度的匹配

        置信度判定：最高分 > 85 且与第二名差距 > 15 → confident=True

        Returns:
            {best_match, confident, candidates}
        """
        candidates = self.search(query, entity_type=entity_type, top_k=5, threshold=50.0)

        if not candidates:
            return {
                'best_match': None,
                'confident': False,
                'candidates': [],
            }

        best = candidates[0]
        second_score = candidates[1]['score'] if len(candidates) > 1 else 0
        confident = best['score'] > 85 and (best['score'] - second_score) > 15

        return {
            'best_match': best,
            'confident': confident,
            'candidates': candidates,
        }

    def invalidate_cache(self):
        """写操作后调用，清除缓存"""
        self._cache = None
