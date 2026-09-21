"""口播符号词、可配置同音表、编号上下文里的中文数字。

补的是两类现场录音里稳定复现的 ASR 噪声：

1. 编号里的 ``-`` 被念成「横杠/斜杠/破折号/减号/杠」，ASR 如实转写成汉字；
   连字符一旦丢了，"一零杠八" 这种短数字串也不再满足 >=3 的转换门槛。
2. 同音字选错（"丝通" / "四通"）。错法与声学模型、口音绑定，不同现场不一样，
   所以词表走配置 ``asr_synonyms``，代码里默认空表。

边界与 ``normalize.py`` 顶部一致：只还原用户本意，不做会改变字面值的激进
归一化 —— 物料名里的「杠」「四通」必须原样活下来。
"""

import os
import sys

import pytest

_MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from providers.normalize import (  # noqa: E402
    apply_synonyms,
    cn_digits_in_code_context,
    configure_synonyms,
    dash_words_to_hyphen,
    get_synonyms,
    normalize_query,
)


@pytest.fixture(autouse=True)
def _reset_synonyms():
    """同音表是进程级状态，测完还原，避免污染同批次其它用例。"""
    before = get_synonyms()
    yield
    configure_synonyms(before)


class TestDashWords:
    """口播符号词 → ``-``，但只在编号上下文里。"""

    @pytest.mark.parametrize("src,want", [
        ("一零杠八", "一零-八"),
        ("BCL横杠02", "BCL-02"),
    ])
    def test_converts_in_code_context(self, src, want):
        assert dash_words_to_hyphen(src) == want

    @pytest.mark.parametrize("src", [
        "杠上开花",          # 「杠」是普通句子里的常用字
        "抬杠",
        "三减号通",          # 一侧是汉字：符号词是物料名的一部分
        "减号键",            # 开头没有编号字符
        "4IO-2.0-3.2-12-A",  # 没有符号词，字面值原样
    ])
    def test_leaves_plain_text_alone(self, src):
        assert dash_words_to_hyphen(src) == src

    def test_longest_word_wins(self):
        """「斜杠」不能被「杠」抢先吃掉半个词。"""
        assert dash_words_to_hyphen("A斜杠B") == "A-B"
        assert dash_words_to_hyphen("A破折号B") == "A-B"

    def test_existing_hyphens_are_never_touched(self):
        """只替换符号词本身，原有的 '--' 是编号的一部分，不做去重/合并。"""
        assert dash_words_to_hyphen("A--B杠C") == "A--B-C"

    @pytest.mark.parametrize("src,want", [
        ("一零杠八四通", "一零-八四通"),   # 两侧都是中文数字字
        ("ABC杠12", "ABC-12"),
    ])
    def test_both_sides_code_chars(self, src, want):
        assert dash_words_to_hyphen(src) == want


class TestSynonyms:
    """同音/误听词表，缺省空表。"""

    def test_replaces_by_table(self):
        assert apply_synonyms("丝通阀", {"丝通": "四通"}) == "四通阀"

    def test_correct_text_is_untouched(self):
        assert apply_synonyms("四通阀", {"丝通": "四通"}) == "四通阀"

    def test_empty_table_is_noop(self):
        assert apply_synonyms("丝通阀", {}) == "丝通阀"

    def test_no_cascade(self):
        """一次性替换：替换产物不再参与匹配，否则 甲→乙→丙 会级联。"""
        assert apply_synonyms("甲乙", {"甲": "乙", "乙": "丙"}) == "乙丙"

    def test_default_table_is_empty_until_configured(self):
        configure_synonyms({})
        assert normalize_query("丝通阀") == "丝通阀"
        configure_synonyms({"丝通": "四通"})
        assert normalize_query("丝通阀") == "四通阀"

    def test_invalid_entries_are_dropped(self):
        """配置是人手写的，一个笔误不该让 MCP 起不来。"""
        configure_synonyms({"": "x", 1: "y", "丝通": None, "司通": "四通"})
        assert get_synonyms() == {"司通": "四通"}


class TestNormalizeQueryWithAsrNoise:
    """组合入口：口播符号词 + 中文数字 + 汉字上下文保护。"""

    @pytest.mark.parametrize("src,want", [
        # 「四通」是物料名的一部分，不能被转成 "4通" / "84通"
        ("10-8四通", "10-8四通"),
        # 口播全套：杠 → '-'，被切短的数字串也要转，但「四通」留住
        ("一零杠八四通", "10-8四通"),
        # 已有的长串规则不受影响
        ("幺零零二零一", "100201"),
        # 字母前缀后的短数字串（两位）也要转
        ("BCL零二", "BCL02"),
    ])
    def test_normalize(self, src, want):
        assert normalize_query(src) == want

    @pytest.mark.parametrize("src", [
        "10-8四通", "一零杠八四通", "BCL零二", "杠上开花", "三通阀",
    ])
    def test_idempotent(self, src):
        once = normalize_query(src)
        assert normalize_query(once) == once

    @pytest.mark.parametrize("src", [
        "三通", "四氟垫片", "二三", "三十五", "A-三十五个",
    ])
    def test_short_runs_without_code_context_stay(self, src):
        """没有编号上下文的短数字串不转，位词相邻的是计量不是编号。"""
        assert cn_digits_in_code_context(src) == src
