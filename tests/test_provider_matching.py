"""Provider 公共匹配层。

重构前 parts_wms 和 mock_wms 各写了一份 ~130 行的 difflib 匹配，逐行相似、
只差字段名和权重；本地模式 DefaultProvider 走服务端 FuzzyMatcher，用的是
rapidfuzz + pypinyin，能力又强一截。同一句话在三条路径上表现不同。

这些用例锁定统一后的行为：精确匹配不回退、谐音容错生效、无关词不误报。
"""

import os
import re
import sys

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
