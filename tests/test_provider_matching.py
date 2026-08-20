"""Provider 公共匹配层。

重构前 parts_wms 和 mock_wms 各写了一份 ~130 行的 difflib 匹配，逐行相似、
只差字段名和权重；本地模式 DefaultProvider 走服务端 FuzzyMatcher，用的是
rapidfuzz + pypinyin，能力又强一截。同一句话在三条路径上表现不同。

这些用例锁定统一后的行为：精确匹配不回退、谐音容错生效、无关词不误报。
"""

import os
import re
import sys
import time

import pytest

_MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from providers.matching import (  # noqa: E402
    LocalMatchMixin,
    MatchConfig,
    norm_for_match,
    tokenize_for_match,
)


PARTS = [
    {"partName": "上钳口", "partNo": "100201", "partType": "4IO-2.0-3.2-12-A", "stockQty": 3},
    {"partName": "下钳口", "partNo": "100202", "partType": "4IO-2.0-3.2-12-C", "stockQty": 5},
    {"partName": "撬具", "partNo": "LH-815", "partType": "LH-815", "stockQty": 2},
    {"partName": "电极帽", "partNo": "LV0045", "partType": "银色", "stockQty": 0},
    {"partName": "M3螺丝", "partNo": "SKU-0001", "partType": "8mm", "stockQty": 99},
]


class _P(LocalMatchMixin):
    MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})

    def __init__(self, items=None, err=None):
        self._items = PARTS if items is None else items
        self._err = err

    def _fetch_items(self):
        return (None, self._err) if self._err else (self._items, None)


@pytest.fixture(autouse=True)
def _clear_cache():
    """缓存是进程级的，测试之间必须隔离，否则互相污染。"""
    from providers.matching import _item_cache
    _item_cache.invalidate()
    yield
    _item_cache.invalidate()


@pytest.fixture
def p():
    return _P()


class TestNormalization:
    def test_strips_punctuation_for_matching(self):
        """与工具入口那层的边界不同：这里去标点，让 4IO-2.0 和 4IO2.0 对上。"""
        assert norm_for_match("4IO-2.0-3.2-12-A") == norm_for_match("4IO2.03.212A")

    def test_applies_itn(self):
        assert norm_for_match("一零零二零一") == "100201"

    def test_lowercases(self):
        assert norm_for_match("LV0045") == norm_for_match("lv0045")

    def test_tokenize_inserts_cjk_ascii_boundary(self):
        assert tokenize_for_match("银色M3螺丝") == "银色 m3 螺丝"

    @pytest.mark.parametrize("v", [None, "", 0])
    def test_handles_empty(self, v):
        assert norm_for_match(v) == ""
        assert tokenize_for_match(v) == ""


class TestExactMatch:
    @pytest.mark.parametrize("q,want", [
        ("100201", "100201"),          # 编码
        ("上钳口", "100201"),            # 名称
        ("4IO-2.0-3.2-12-A", "100201"),  # 规格
        ("LV0045", "LV0045"),
        ("lv0045", "LV0045"),           # 大小写无关
        ("一零零二零一", "100201"),        # 中文数字
    ])
    def test_exact_hits(self, p, q, want):
        item, err = p._locate(q)
        assert err is None, f"{q} 未命中: {err}"
        assert item["partNo"] == want

    @pytest.mark.parametrize("q", ["撬具LH-815", "LH-815撬具"])
    def test_name_spec_concatenation_both_orders(self, p, q):
        """口语把名称和规格连读，两种顺序都要认。"""
        item, err = p._locate(q)
        assert err is None and item["partNo"] == "LH-815"


class TestHomophoneTolerance:
    """拼音容错 —— 外部 Provider 重构前没有这个能力。"""

    def test_asr_homophone_resolves(self, p):
        """「电极帽」被 ASR 听成「电级帽」，重构前只能让用户澄清。"""
        item, err = p._locate("电级帽")
        assert err is None, f"应命中而非澄清: {err}"
        assert item["partNo"] == "LV0045"

    def test_homophone_still_ranks_above_floor(self, p):
        """「钳口」听成「前口」，两个钳口同分并列，走澄清是正确的。"""
        item, err = p._locate("前口")
        assert item is None and err["error"] == "ambiguous_name"
        assert len(err["candidates"]) >= 2


class TestNoiseRejection:
    """跟物料无关的话不能召回候选，否则会把 LLM 带偏。

    拼音串都是拉丁字母，任意两个中文词的拼音都有 30~50 的基础相似度。
    不设 _PINYIN_TEXT_GATE 门槛时，这批词实测误报 11/15。
    """

    NOISE = [
        "不存在的东西", "今天天气怎么样", "帮我放首歌", "打开投屏", "你是谁",
        "苹果", "矿泉水", "洗衣机", "北京市朝阳区", "随便说点什么",
        "999999", "查询库存", "出库五个", "仓库在哪",
    ]

    @pytest.mark.parametrize("q", NOISE)
    def test_unrelated_query_returns_not_found(self, p, q):
        item, err = p._locate(q)
        assert item is None
        assert err["error"] == "not_found", f"{q!r} 误召回: {err}"

    def test_pinyin_gate_blocks_zero_text_overlap(self, p):
        """字面零重叠时拼音分不采信。"""
        assert p._score("矿泉水", PARTS[0]) == 0.0


class TestAmbiguity:
    def test_tied_candidates_ask_for_clarification(self):
        items = [
            {"partName": "钳口", "partNo": "A1", "partType": "X"},
            {"partName": "钳口", "partNo": "A2", "partType": "Y"},
        ]
        item, err = _P(items)._locate("钳口")
        assert item is None and err["error"] == "ambiguous_name"
        assert len(err["candidates"]) == 2

    def test_candidates_capped_at_six(self):
        items = [{"partName": f"钳口{i}", "partNo": f"A{i}", "partType": "X"}
                 for i in range(20)]
        _, err = _P(items)._locate("钳口")
        assert len(err["candidates"]) <= 6

    def test_non_fuzzy_requires_exact(self, p):
        """写操作不能替用户猜。"""
        item, err = p._locate("钳口", fuzzy=False)
        assert item is None and err["error"] == "not_found"

        item, err = p._locate("100201", fuzzy=False)
        assert err is None and item["partNo"] == "100201"


class TestFetchFailure:
    def test_fetch_error_propagates_by_default(self):
        boom = {"success": False, "error": "api_error", "message": "对方挂了"}
        item, err = _P(err=boom)._locate("上钳口")
        assert item is None and err is boom

    def test_fallback_hook_can_recover(self):
        """Provider 可覆盖 _locate_fallback，用原始查询词单点查询。"""
        recovered = {"partName": "上钳口", "partNo": "100201", "partType": "X"}

        class WithFallback(_P):
            seen = []

            def _locate_fallback(self, query, err):
                WithFallback.seen.append(query)
                return recovered, None

        item, err = WithFallback(err={"error": "boom"})._locate("4IO-2.0-3.2-12-A")
        assert err is None and item is recovered
        assert WithFallback.seen == ["4IO-2.0-3.2-12-A"], "退化路径必须收到原始词，不能是去标点后的"

    def test_empty_item_list_is_not_found(self):
        item, err = _P([])._locate("上钳口")
        assert item is None and err["error"] == "not_found"


class TestFieldMapping:
    def test_different_field_names(self):
        """同一套逻辑服务不同字段命名 —— 这是客户接入时唯一要写的东西。"""
        class Other(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "name", "code": "code", "spec": "spec"})

            def _fetch_items(self):
                return [{"name": "矿泉水", "code": "SP-001", "spec": "500ml"}], None

        item, err = Other()._locate("SP-001")
        assert err is None and item["name"] == "矿泉水"

    def test_missing_field_is_skipped(self):
        """字段缺失不该抛异常。"""
        class NoSpec(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "name", "code": "code", "spec": None})

            def _fetch_items(self):
                return [{"name": "撬具", "code": "LH-815"}], None

        item, err = NoSpec()._locate("撬具")
        assert err is None and item["code"] == "LH-815"

    def test_weights_are_configurable(self):
        item = {"n": "水", "c": "X", "s": "矿泉水500ml"}
        high = type("H", (LocalMatchMixin,), {
            "MATCH": MatchConfig(fields={"name": "n", "code": "c", "spec": "s"},
                                 weights={"spec": 1.0})})()
        low = type("L", (LocalMatchMixin,), {
            "MATCH": MatchConfig(fields={"name": "n", "code": "c", "spec": "s"},
                                 weights={"spec": 0.3})})()
        assert high._score("矿泉水500ml", item) >= low._score("矿泉水500ml", item)


class TestBackwardCompatibility:
    """老 Provider 覆盖了这些方法的，行为必须不变。"""

    def test_subclass_score_wins(self, p):
        class Override(_P):
            def _score(self, query, item):
                return 1.0 if item["partNo"] == "LH-815" else 0.0

        item, err = Override()._locate("完全不相关的词")
        assert err is None and item["partNo"] == "LH-815"

    def test_subclass_locate_wins(self):
        sentinel = ({"partNo": "ZZZ"}, None)

        class Override(_P):
            def _locate(self, query, fuzzy=True):
                return sentinel

        assert Override()._locate("任意") is sentinel

    def test_fetch_items_is_required(self):
        class Incomplete(LocalMatchMixin):
            pass

        with pytest.raises(NotImplementedError, match="_fetch_items"):
            Incomplete()._locate("x")


class TestRefactoredProvidersUseCommonLayer:
    """防回归：两个 Provider 必须继承公共层，别再各写一份。"""

    @pytest.mark.parametrize("path,cls", [
        ("mcp/providers/custom/1/parts_wms.py", "PartsWmsProvider"),
        ("tests/fixtures/mock_wms/provider.py", "CustomWmsProvider"),
    ])
    def test_inherits_mixin_and_declares_fields(self, path, cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(root, path), encoding="utf-8").read()
        assert f"class {cls}(LocalMatchMixin, BaseProvider)" in src
        assert "MATCH = MatchConfig(" in src

    @pytest.mark.parametrize("path", [
        "mcp/providers/custom/1/parts_wms.py",
        "tests/fixtures/mock_wms/provider.py",
    ])
    def test_no_duplicate_scoring_implementation(self, path):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(root, path), encoding="utf-8").read()
        for gone in ("def _score(", "def _rank(", "def _locate(self, product_name"):
            assert gone not in src, f"{path} 仍保留重复实现: {gone}"
        assert not re.search(r"^import difflib$", src, re.M), \
            f"{path} 仍在 import difflib"


class TestCaching:
    """缓存与实时性。

    缓存最怕「用户新增了物料却搜不到」。方案是「未命中即强制刷新」：命中走
    缓存拿性能，匹配不上就刷新重试一次，新物料第一次查询就能见到。
    """

    @staticmethod
    def _fresh_cache():
        from providers.matching import _item_cache
        _item_cache.invalidate()

    def _make(self, items, ttl=60.0):
        from providers.matching import LocalMatchMixin, MatchConfig

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})
            CACHE_TTL = ttl

            def __init__(self):
                self.config = {"api_base_url": "http://t", "external_tenant_id": "T1"}
                self.fetch_count = 0
                self.items = list(items)

            def _fetch_items(self):
                self.fetch_count += 1
                return list(self.items), None

        self._fresh_cache()
        return P()

    def test_repeated_queries_hit_cache(self):
        """一轮对话里连调多次工具，只应拉一次全量。"""
        p = self._make(PARTS)
        for _ in range(5):
            p._locate("上钳口")
        assert p.fetch_count == 1, f"拉取了 {p.fetch_count} 次，应为 1"

    def test_new_item_found_without_waiting_for_ttl(self):
        """核心场景：用户刚在 ERP 新增物料，立刻查询就要能查到。"""
        p = self._make(PARTS, ttl=3600)      # TTL 很长，只能靠强制刷新
        p._locate("上钳口")                   # 预热缓存
        assert p.fetch_count == 1

        p.items.append({"partName": "新到货扳手", "partNo": "NEW001", "partType": "M8"})
        item, err = p._locate("新到货扳手")

        assert err is None, f"新增物料应能查到，实际: {err}"
        assert item["partNo"] == "NEW001"
        assert p.fetch_count == 2, "未命中时应触发一次强制刷新"

    def test_refresh_is_debounced(self):
        """连问几个不存在的东西，不能每次都去打对方接口。"""
        p = self._make(PARTS)
        p._locate("上钳口")
        base = p.fetch_count
        for _ in range(5):
            p._locate("库里绝对没有的东西")
        assert p.fetch_count - base == 1, \
            f"防抖失效，额外拉取了 {p.fetch_count - base} 次"

    def test_ttl_expiry_refetches(self):
        p = self._make(PARTS, ttl=0.0)       # 立即过期
        p._locate("上钳口")
        p._locate("上钳口")
        assert p.fetch_count == 2

    def test_stale_cache_used_when_fetch_fails(self):
        """对方接口抖动时用过期数据兜底，而不是让用户完全查不了。"""
        p = self._make(PARTS, ttl=0.0)
        p._locate("上钳口")                   # 建立缓存

        boom = {"success": False, "error": "api_error", "message": "对方挂了"}
        p._fetch_items = lambda: (None, boom)

        item, err = p._locate("上钳口")
        assert err is None and item["partNo"] == "100201", "应回退到过期缓存"

    def test_first_fetch_failure_propagates(self):
        """没有任何缓存时，拉取失败必须如实上报。"""
        p = self._make(PARTS)
        boom = {"success": False, "error": "api_error", "message": "对方挂了"}
        p._fetch_items = lambda: (None, boom)
        item, err = p._locate("上钳口")
        assert item is None and err is boom

    def test_cache_isolated_by_tenant(self):
        """多租户不能串数据。"""
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh_cache()

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})

            def __init__(self, tenant, items):
                self.config = {"api_base_url": "http://t", "external_tenant_id": tenant}
                self.items = items

            def _fetch_items(self):
                return self.items, None

        a = P("T1", [{"partName": "液压油缸", "partNo": "A1", "partType": "X"}])
        b = P("T2", [{"partName": "钨钢铣刀", "partNo": "B1", "partType": "Y"}])
        assert a._locate("液压油缸")[0]["partNo"] == "A1"
        assert b._locate("钨钢铣刀")[0]["partNo"] == "B1"
        assert a._locate("钨钢铣刀")[0] is None, "A 租户不该看到 B 租户的物料"
        assert b._locate("液压油缸")[0] is None, "B 租户不该看到 A 租户的物料"

    def test_invalidate_cache_forces_refetch(self):
        p = self._make(PARTS, ttl=3600)
        p._locate("上钳口")
        p.invalidate_cache()
        p._locate("上钳口")
        assert p.fetch_count == 2

    def test_indexed_scoring_matches_plain_scoring(self):
        """快慢两条打分路径结果必须一致，否则缓存会悄悄改变匹配行为。"""
        from providers.matching import _Indexed, _Query
        p = self._make(PARTS)
        for q in ["上钳口", "100201", "前口", "电级帽", "4IO-2.0-3.2-12-A", "矿泉水"]:
            qq = _Query(q)
            for it in PARTS:
                plain = p._score(qq, it)
                fast = p._score_indexed(qq, _Indexed(it, p.MATCH.fields))
                assert abs(plain - fast) < 1e-9, \
                    f"{q!r} vs {it['partNo']}: 慢路径 {plain} != 快路径 {fast}"

    def test_custom_score_subclass_uses_slow_path(self):
        """子类覆盖了 _score 时必须走原始 dict，不能被预计算索引静默绕过。"""
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh_cache()
        seen = []

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})

            def __init__(self):
                self.config = {"api_base_url": "http://t"}

            def _fetch_items(self):
                return list(PARTS), None

            def _score(self, query, item):
                seen.append(item)
                return 1.0 if item["partNo"] == "LH-815" else 0.0

        item, err = P()._locate("随便什么")
        assert err is None and item["partNo"] == "LH-815"
        assert seen and all(isinstance(x, dict) for x in seen), "子类应收到原始 dict"

    def test_stock_number_is_never_served_from_cache(self):
        """缓存只回答「是哪一条」，不回答「这条现在有多少」。

        用户刚出库 5 件、紧接着问库存，直接返回缓存记录就会报出库前的数字。
        这个问题实测表现为 e2e 用例 test_connection_binding_beats_provider_
        stored_config 在全量跑时失败：前一个用例入库 7 件，后一个用例从缓存
        读到了入库前的库存。
        """
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh_cache()
        live = {"partName": "上钳口", "partNo": "100201", "partType": "X", "stockQty": 10}

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})
            CACHE_TTL = 3600

            def __init__(self):
                self.config = {"api_base_url": "http://t"}

            def _fetch_items(self):
                return [dict(live)], None

            def _refresh_item(self, item):
                return dict(live) if item.get("partNo") == live["partNo"] else item

        p = P()
        assert p._locate("上钳口")[0]["stockQty"] == 10

        live["stockQty"] = 3           # 出库 7 件，缓存里还是 10
        item, err = p._locate("上钳口")
        assert err is None
        assert item["stockQty"] == 3, "库存数字来自缓存，应取实时值"

    def test_refresh_item_failure_falls_back_to_cached_record(self):
        """取实时数据失败时用缓存记录兜底，不能让整个查询失败。"""
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh_cache()

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})

            def __init__(self):
                self.config = {"api_base_url": "http://t"}

            def _fetch_items(self):
                return [{"partName": "上钳口", "partNo": "100201", "partType": "X", "stockQty": 9}], None

            def _refresh_item(self, item):
                return item      # 模拟对方接口取不到，返回原记录

        item, err = P()._locate("上钳口")
        assert err is None and item["stockQty"] == 9


class TestReviewFindings:
    """独立审核发现的缺陷，逐条锁死。

    这些用例对应的 bug 全部从原有测试的盲区里逃出来了：原有用例都是单线程、
    无重复编码、无「缓存有近似项 + 新增精确项」、不覆盖 search 的非默认参数。
    """

    @staticmethod
    def _fresh():
        from providers.matching import _item_cache
        _item_cache.invalidate()

    # ── P0-1 ──

    @pytest.mark.parametrize("path,cls", [
        ("mcp/providers/custom/1/parts_wms.py", "PartsWmsProvider"),
        ("tests/fixtures/mock_wms/provider.py", "CustomWmsProvider"),
    ])
    def test_no_dangling_norm_reference(self, path, cls):
        """删掉模块级 _norm 后，search() 里的引用没跟着改 → NameError。

        MCP 层恒传 fuzzy=True 且 status 在 exclude_args 里，所以线上不可达，
        但任何非 MCP 调用方（backend、probe 扩展）一碰就崩。
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(root, path), encoding="utf-8").read()
        assert not re.search(r"(?<![\w.])_norm\s*\(", src), \
            f"{path} 仍引用已删除的 _norm"

    def test_search_with_status_filter_does_not_crash(self):
        """走 search 的非默认参数路径，这正是 P0-1 逃逸的地方。"""
        import importlib.util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "pw_status", os.path.join(root, "mcp/providers/custom/1/parts_wms.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        p = m.PartsWmsProvider({"api_base_url": "http://x", "auth": {}})
        p._fetch_products = lambda: ([
            {"partName": "上钳口", "partNo": "100201", "partType": "X", "stockQty": 1},
        ], None)
        for kwargs in ({"status": "low"}, {"fuzzy": False}, {"status": "正常", "fuzzy": False}):
            r = p.search(query="上钳口", entity_type="material", category=None,
                         contact_type=None, include_batches=False, max_results=10,
                         **{"status": None, "fuzzy": True, **kwargs})
            assert isinstance(r, dict)

    # ── P0-2 ──

    def test_new_exact_item_beats_stale_near_match(self):
        """缓存里有 0.9 分的旧近似项时，也必须刷新。

        用户在 ERP 新增「上钳口B」后立刻查它，缓存里只有「上钳口A」——
        A 能拿到 0.9 分（>= confident 0.75）且唯一，旧判据 `if not ranked`
        不会触发刷新，于是返回 A。出库就是出错货，而且全程 success。
        """
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh()
        items = [{"partName": "上钳口A", "partNo": "A001", "partType": "X"}]

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})
            CACHE_TTL = 3600

            def __init__(self):
                self.config = {"api_base_url": "http://t", "external_tenant_id": "T"}

            def _fetch_items(self):
                return list(items), None

        p = P()
        p._locate("上钳口A")                       # 预热缓存
        items.append({"partName": "上钳口B", "partNo": "B001", "partType": "Y"})

        item, err = p._locate("上钳口B")
        assert err is None, f"应命中新增的 B，实际: {err}"
        assert item["partNo"] == "B001", f"命中了旧的近似项 {item['partNo']}，会出错货"

    def test_exact_hit_still_skips_refresh(self):
        """精确命中不该触发刷新，否则每次查询都多打一次对方接口。"""
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh()

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})
            CACHE_TTL = 3600

            def __init__(self):
                self.config = {"api_base_url": "http://t", "external_tenant_id": "T2"}
                self.n = 0

            def _fetch_items(self):
                self.n += 1
                return [{"partName": "上钳口", "partNo": "100201", "partType": "X"}], None

        p = P()
        for _ in range(4):
            p._locate("100201")
        assert p.n == 1, f"精确命中不该刷新，实际拉取 {p.n} 次"

    # ── P0-3 ──

    def test_duplicate_codes_are_deduped(self):
        """取全量的接口不保证每个物料一行，多仓/多批次会返回同一编码多条。

        不去重的话两条都是 1.0 分 → 并列 → 返回「上钳口（X）、上钳口（X）」
        这种一模一样的候选，用户报编号也澄清不掉，对话卡死。
        """
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh()

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})

            def __init__(self):
                self.config = {"api_base_url": "http://t", "external_tenant_id": "T3"}

            def _fetch_items(self):
                return [
                    {"partName": "上钳口", "partNo": "100201", "partType": "X", "warehouse": "甲"},
                    {"partName": "上钳口", "partNo": "100201", "partType": "X", "warehouse": "乙"},
                ], None

        item, err = P()._locate("100201")
        assert err is None, f"同编码重复行应去重后命中，实际: {err}"
        assert item["partNo"] == "100201"

    def test_genuinely_different_items_still_ask(self):
        """去重不能把真正不同的物料也合并掉。"""
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh()

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})

            def __init__(self):
                self.config = {"api_base_url": "http://t", "external_tenant_id": "T4"}

            def _fetch_items(self):
                return [
                    {"partName": "钳口", "partNo": "A1", "partType": "X"},
                    {"partName": "钳口", "partNo": "A2", "partType": "Y"},
                ], None

        item, err = P()._locate("钳口")
        assert item is None and err["error"] == "ambiguous_name"
        assert len({c["extra"]["sku"] for c in err["candidates"]}) == 2

    # ── P1-4 ──

    def test_concurrent_cold_start_fetches_once(self):
        """线程池并发下 single-flight：8 线程冷启动只应拉一次。

        MCP 工具走 asyncio.to_thread，并发是常态而非极端。
        """
        import threading
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh()
        calls = []
        lock = threading.Lock()

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})
            CACHE_TTL = 3600

            def __init__(self):
                self.config = {"api_base_url": "http://t", "external_tenant_id": "TC"}

            def _fetch_items(self):
                with lock:
                    calls.append(1)
                time.sleep(0.05)          # 放大竞态窗口
                return [{"partName": "上钳口", "partNo": "100201", "partType": "X"}], None

        p = P()
        threads = [threading.Thread(target=lambda: p._locate("100201")) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1, f"并发冷启动拉取了 {len(calls)} 次，single-flight 失效"

    def test_concurrent_misses_respect_debounce(self):
        """并发的 not_found 不能各自触发一次强制刷新。"""
        import threading
        from providers.matching import LocalMatchMixin, MatchConfig
        self._fresh()
        calls = []
        lock = threading.Lock()

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "partName", "code": "partNo", "spec": "partType"})
            CACHE_TTL = 3600

            def __init__(self):
                self.config = {"api_base_url": "http://t", "external_tenant_id": "TD"}

            def _fetch_items(self):
                with lock:
                    calls.append(1)
                time.sleep(0.05)
                return [{"partName": "上钳口", "partNo": "100201", "partType": "X"}], None

        p = P()
        p._locate("100201")                      # 预热，calls = 1
        base = len(calls)
        threads = [threading.Thread(target=lambda: p._locate("库里没有的东西"))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) - base <= 1, \
            f"并发 miss 触发了 {len(calls) - base} 次强制刷新，防抖被绕过"

    # ── P1-5 ──

    def test_cache_key_includes_warehouse_id(self):
        """parts_wms 用的是 warehouse_id 而非 external_warehouse_id。

        只按后者隔离的话，同一 ERP 地址下绑不同仓库的连接会共用缓存。
        """
        from providers.matching import LocalMatchMixin, MatchConfig

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "n", "code": "c", "spec": "s"})

            def __init__(self, wh):
                self.config = {"api_base_url": "http://same"}
                self.warehouse_id = wh

            def _fetch_items(self):
                return [], None

        assert P("WH-BJ")._cache_key() != P("WH-SH")._cache_key()

    def test_cache_key_includes_auth(self):
        """同一 URL 不同凭据，可见的数据范围可能不同。"""
        from providers.matching import LocalMatchMixin, MatchConfig

        class P(LocalMatchMixin):
            MATCH = MatchConfig(fields={"name": "n", "code": "c", "spec": "s"})

            def __init__(self, key):
                self.config = {"api_base_url": "http://same",
                               "auth": {"type": "api_key", "key": key}}

            def _fetch_items(self):
                return [], None

        a, b = P("secret-a")._cache_key(), P("secret-b")._cache_key()
        assert a != b
        assert not any("secret" in str(x) for x in a), "凭据明文进了缓存 key"
