"""查询词归一化：纯函数行为 + 工具入口装饰器 + 跨模块一致性。

背景（2026-08-19 现场故障）：设备念「按照型号幺零零二零一」，ASR 转写成
中文数字、LLM 又把「型号」当成名称的一部分传进来，最终 ``product_name`` 是
``"型号幺零零二零一"``，外部 ERP 侧 difflib 打分连候选下限（0.34）都到不了，
返回「未找到备品」。

修复放在 MCP 工具入口而不是某个 Provider 里 —— 这两类噪声与后端是谁无关，
放在入口处客户自己写的 Provider 一行不用改就能受益。
"""

import os
import sys

import pytest

_MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from providers.normalize import (  # noqa: E402
    cn_digits_to_arabic,
    fullwidth_to_halfwidth,
    normalize_query,
    strip_query_prefix,
)


class TestChineseNumerals:
    """ASR 把编号逐字念出来，WMS 里存的是阿拉伯数字。"""

    @pytest.mark.parametrize("src,want", [
        ("一零零二零一", "100201"),
        ("幺零零二零一", "100201"),          # 口语把 1 念成「幺」
        ("幺九五零", "1950"),
        ("一二三四五六七八九零", "1234567890"),
        ("查询型号一零零二零一的库存", "查询型号100201的库存"),
    ])
    def test_converts_digit_runs(self, src, want):
        assert cn_digits_to_arabic(src) == want

    @pytest.mark.parametrize("src", [
        "三通",              # 单个数字字，不足 3 个
        "一字螺丝刀",
        "四氟垫片",
        "六角螺栓",
        "第一批次",
        "二三",              # 只有 2 个
        "三十五",            # 含位词，是计量不是编号
        "一二三十五",        # 数字串后紧邻「十」，整段不转
        "两百三十五个",
        "100201",            # 已是阿拉伯数字
        "上钳口",
    ])
    def test_leaves_non_serial_text_intact(self, src):
        assert cn_digits_to_arabic(src) == src


class TestQueryPrefix:
    """LLM 把用户话里的引导词一起塞进参数。"""

    @pytest.mark.parametrize("src,want", [
        ("型号100201", "100201"),
        ("规格4IO-2.0-3.2-12-A", "4IO-2.0-3.2-12-A"),
        ("物料编码：ABC-123", "ABC-123"),
        ("备件号 X9", "X9"),
        ("SKU 0001", "0001"),
        ("sku0001", "0001"),
        ("part no. 55", "55"),
    ])
    def test_strips_leading_prefix(self, src, want):
        assert strip_query_prefix(src) == want

    def test_longest_prefix_wins(self):
        """「物料编码」不该被「编码」抢先匹配掉一半。"""
        assert strip_query_prefix("物料编码ABC") == "ABC"

    def test_prefix_only_input_keeps_original(self):
        """剥完为空则保留原文，否则查询彻底失去意义。"""
        assert strip_query_prefix("型号") == "型号"
        assert normalize_query("型号") == "型号"

    @pytest.mark.parametrize("src", [
        "上钳口",
        "M3 螺丝",
        "1950110011073A",
        "查询型号100201的库存",   # 前缀不在开头，整句不动
    ])
    def test_leaves_other_text_intact(self, src):
        assert strip_query_prefix(src) == src


class TestFullwidth:
    @pytest.mark.parametrize("src,want", [
        ("ＡＢＣ－１２３", "ABC-123"),
        ("１００２０１", "100201"),
        ("　", " "),                    # 全角空格
    ])
    def test_converts_fullwidth_ascii(self, src, want):
        assert fullwidth_to_halfwidth(src) == want

    @pytest.mark.parametrize("src", [
        "，。、",          # 中文标点
        "（银色）",        # 全角括号：物料名里是内容的一部分
        "：；！？",
    ])
    def test_leaves_cjk_punctuation_alone(self, src):
        """不转全角标点。

        全角括号/逗号也落在 0xFF01-0xFF5E 区间，无脑整段转会把
        "电极帽（银色）" 改写成 "电极帽(银色)"，若 ERP 存的是全角，
        精确匹配就断了。
        """
        assert fullwidth_to_halfwidth(src) == src


class TestNormalizeQuery:
    """组合入口，顺序敏感。"""

    @pytest.mark.parametrize("src,want", [
        # 现场故障的真实输入
        ("型号幺零零二零一", "100201"),
        ("型号一零零二零一", "100201"),
        # 前缀要在 ITN 之后剥：前缀正则不认中文数字
        ("规格幺九五零", "1950"),
        # 全角要最先转：否则前缀正则的 [\s:：] 吃不到全角冒号
        ("型号：１００２０１", "100201"),
        ("ＳＫＵ　０００１", "0001"),
        # 不该动的
        ("上钳口", "上钳口"),
        ("三通阀", "三通阀"),
        ("M3 螺丝", "M3 螺丝"),
        ("1950110011073A", "1950110011073A"),
        ("100201", "100201"),
    ])
    def test_normalize(self, src, want):
        assert normalize_query(src) == want

    @pytest.mark.parametrize("src", [
        "型号幺零零二零一", "上钳口", "型号", "100201",
        "物料编码：ABC-123", "三十五", "",
    ])
    def test_idempotent(self, src):
        once = normalize_query(src)
        assert normalize_query(once) == once

    @pytest.mark.parametrize("value", [None, 123, 0, [], {}, True])
    def test_non_string_passes_through(self, value):
        """不做类型强转：静默转换会掩盖上游的错误。"""
        assert normalize_query(value) is value

    def test_preserves_literal_for_erp_exact_lookup(self):
        """不做去标点等激进归一化。

        parts_wms 在拉全量列表失败时会退化成拿这个词直接调 ERP 的精确查询
        接口，字面值被破坏就查不到了。去标点属于各 Provider 的匹配层。
        """
        assert normalize_query("4IO-2.0-3.2-12-A") == "4IO-2.0-3.2-12-A"
        assert normalize_query("A-01/B-02") == "A-01/B-02"
        assert normalize_query("电极帽（银色）") == "电极帽（银色）"


class TestConsistencyWithFuzzyMatch:
    """两处 ITN 实现必须行为一致，否则本地模式和外部 ERP 模式理解不同。

    backend/fuzzy_match.py 的那份在匹配层（去标点后再转），
    mcp/providers/normalize.py 的这份在工具入口。规则一旦漂移，同一句话
    在两种部署模式下会得到不同结果，且极难排查。
    """

    def test_matches_fuzzy_match_itn(self):
        backend_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"
        )
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from fuzzy_match import _cn_digits_to_arabic as backend_itn

        samples = [
            "一零零二零一", "幺零零二零一", "幺九五零", "一二三四五六七八九零",
            "三通", "一字螺丝刀", "四氟垫片", "六角螺栓", "第一批次",
            "二三", "三十五", "一二三十五", "两百三十五个",
            "100201", "上钳口", "M3 螺丝", "1950110011073A",
            "查询型号一零零二零一的库存", "",
        ]
        mismatched = [
            (s, cn_digits_to_arabic(s), backend_itn(s))
            for s in samples
            if cn_digits_to_arabic(s) != backend_itn(s)
        ]
        assert not mismatched, f"ITN 规则已漂移: {mismatched}"


class TestToolDecorator:
    """装饰器在工具入口生效，且客户 Provider 无需改动。"""

    @staticmethod
    def _make(param_names, fn):
        import warehouse_mcp
        return warehouse_mcp._normalize_query_args(*param_names)(fn)

    def test_normalizes_keyword_argument(self):
        seen = {}

        def tool(product_name=None):
            seen["v"] = product_name
            return {"success": True}

        self._make(["product_name"], tool)(product_name="型号幺零零二零一")
        assert seen["v"] == "100201"

    def test_normalizes_positional_argument(self):
        """MCP 走 kwargs，但内部/测试可能用位置参数。"""
        seen = {}

        def tool(product_name):
            seen["v"] = product_name
            return {"success": True}

        self._make(["product_name"], tool)("型号幺零零二零一")
        assert seen["v"] == "100201"

    def test_leaves_untargeted_params_alone(self):
        seen = {}

        def tool(product_name=None, reason_note=None):
            seen.update(product_name=product_name, reason_note=reason_note)
            return {"success": True}

        self._make(["product_name"], tool)(
            product_name="型号幺零零二零一", reason_note="一零零二零一"
        )
        assert seen["product_name"] == "100201"
        assert seen["reason_note"] == "一零零二零一", "未标注的参数不该被改写"

    def test_multiple_params(self):
        seen = {}

        def tool(batch_no=None, new_location=None):
            seen.update(batch_no=batch_no, new_location=new_location)
            return {"success": True}

        self._make(["batch_no", "new_location"], tool)(
            batch_no="编号幺二三", new_location="库位四五六"
        )
        assert seen["batch_no"] == "123"
        assert seen["new_location"] == "库位456"

    def test_missing_optional_param_is_skipped(self):
        def tool(query=None, entity_type="material"):
            return {"success": True, "q": query}

        assert self._make(["query"], tool)(entity_type="material")["q"] is None

    def test_unknown_kwarg_passes_through_to_downstream(self):
        """LLM 编造 schema 外的参数时，由 _antihallucination 的 TypeError
        分支给出结构化提示，归一化层不抢先报错。"""
        def tool(product_name=None):
            return {"success": True}

        with pytest.raises(TypeError):
            self._make(["product_name"], tool)(product_name="x", bogus=1)

    def test_return_value_untouched(self):
        sentinel = {"success": True, "product": {"name": "上钳口"}}

        def tool(product_name=None):
            return sentinel

        assert self._make(["product_name"], tool)(product_name="型号100201") is sentinel

    def test_preserves_function_metadata(self):
        """FastMCP 从签名和 docstring 生成 tool schema，不能被装饰器吃掉。"""
        def tool(product_name: str) -> dict:
            """按产品名查库存。"""
            return {}

        wrapped = self._make(["product_name"], tool)
        assert wrapped.__name__ == "tool"
        assert wrapped.__doc__ == "按产品名查库存。"


class TestToolsAreDecorated:
    """防回归：新增工具时别忘了挂装饰器。"""

    EXPECTED = {
        "resolve_name": ["text"],
        "query_stock": ["product_name"],
        "query_batch": ["batch_no"],
        "stock_in": ["product_name"],
        "stock_out": ["product_name"],
        "search": ["query"],
        "move_batch_location": ["batch_no", "new_location"],
    }

    def test_all_text_taking_tools_declare_normalization(self):
        import re
        src = open(
            os.path.join(_MCP_DIR, "warehouse_mcp.py"), encoding="utf-8"
        ).read()
        missing = []
        for fname, params in self.EXPECTED.items():
            pat = (
                r'@_normalize_query_args\(([^)]*)\)\s*\n'
                r'@_antihallucination\("' + re.escape(fname) + r'"\)'
            )
            m = re.search(pat, src)
            if not m:
                missing.append(f"{fname}: 缺少 @_normalize_query_args")
                continue
            declared = re.findall(r'"([^"]+)"', m.group(1))
            if declared != params:
                missing.append(f"{fname}: 参数为 {declared}，期望 {params}")
        assert not missing, "\n".join(missing)

    def test_decorator_sits_below_log_mcp_call(self):
        """log_mcp_call 是 async wrapper，归一化必须在它下面的同步层。"""
        import re
        src = open(
            os.path.join(_MCP_DIR, "warehouse_mcp.py"), encoding="utf-8"
        ).read()
        for fname in self.EXPECTED:
            pat = (
                r'@log_mcp_call\s*\n@_normalize_query_args\([^)]*\)\s*\n'
                r'@_antihallucination\("' + re.escape(fname) + r'"\)'
            )
            assert re.search(pat, src), f"{fname} 的装饰器顺序不对"
