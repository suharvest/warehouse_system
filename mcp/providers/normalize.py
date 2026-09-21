"""查询词归一化 —— 与后端无关的公共前置处理。

语音链路送进来的查询词有两类固定噪声，跟 WMS 是本地库还是外部 ERP 无关：

1. **中文数字**：ASR 把编号逐字转写成 "一零零二零一" / "幺零零二零一"，
   而所有 WMS 里存的都是 "100201"。
2. **提示性前缀**：LLM 把用户话里的引导词一起塞进参数，
   "按型号100201查" → ``product_name="型号100201"``。

这两件事在每个 Provider 里各修一遍是重复劳动，且实现必然漂移 —— 本地
``FuzzyMatcher`` 有拼音容错和 SKU boost，``parts_wms`` 只有 difflib，同样的
输入在两边表现完全不同。所以统一在 MCP 工具入口做一次，所有 Provider
（包括客户自己写的）拿到的都是已经规整过的词，**客户 Provider 无需任何改动**。

**边界**：这里只做「还原用户本意」，不做匹配用的激进归一化。去括号、去横杠、
拆 token 那些会改变字面值，而归一化结果要原样传给 Provider —— ``parts_wms``
在拉全量列表失败时会退化成拿这个词直接调 ERP 的精确查询接口
(``parts_wms.py`` 的 ``_locate`` 退化分支)，字面值被破坏就查不到了。
去标点属于各 Provider 匹配层的事，由它们自己决定（本地模式的
``FuzzyMatcher._normalize`` 就在匹配时去标点，那是对的）。

所有函数都是纯函数且幂等：``f(f(x)) == f(x)``。
"""

import re

__all__ = [
    "cn_digits_to_arabic",
    "cn_digits_in_code_context",
    "dash_words_to_hyphen",
    "apply_synonyms",
    "configure_synonyms",
    "get_synonyms",
    "strip_query_prefix",
    "fullwidth_to_halfwidth",
    "normalize_query",
]


# ── 中文数字 → 阿拉伯数字 ──
#
# 规则必须与 backend/fuzzy_match.py 的 _cn_digits_to_arabic 保持一致，
# 否则本地模式和外部 ERP 模式对同一句话的理解会不同。
# tests/test_query_normalization.py::test_matches_fuzzy_match_itn 锁定这一点。

_CN_DIGIT_MAP = str.maketrans({
    '零': '0', '〇': '0', '一': '1', '幺': '1', '二': '2', '三': '3',
    '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9',
})

# 只转连续 >=3 个的纯数字单字，且前后不紧邻「十百千万亿」。
#
# >=3 的门槛避开物料名里的单字数字："三通" "一字螺丝刀" "四氟垫片" "六角螺栓"。
# 位词的前后瞻避开计量表达："三十五" 是数量不是编号；"一二三十五" 里的
# "一二三" 后面紧跟「十」，整段也不转。
#
# "幺" 收进来是因为口语报编号常把 1 念成「幺」，ASR 会如实转写。
# "两" 不收：口语里 "两百三十五个" 是计量，收进来弊大于利。
_CN_DIGIT_RUN = re.compile(
    r'(?<![十百千万亿])[零〇一幺二三四五六七八九]{3,}(?![十百千万亿])'
)


def cn_digits_to_arabic(text: str) -> str:
    """把连续的中文数字单字串转成阿拉伯数字，其余原样保留。

    >>> cn_digits_to_arabic('查询型号一零零二零一的库存')
    '查询型号100201的库存'
    >>> cn_digits_to_arabic('三通')
    '三通'
    >>> cn_digits_to_arabic('三十五')
    '三十五'
    """
    if not text:
        return text
    return _CN_DIGIT_RUN.sub(lambda m: m.group().translate(_CN_DIGIT_MAP), text)


# ── 编号上下文里的短中文数字串 ──
#
# ``cn_digits_to_arabic`` 的 >=3 门槛在编号被连字符切断时不够用：
# "一零杠八" 口播出来是 "10-8"，但两段各只有 2 个和 1 个数字字。
# 这里补一条「上下文触发」规则：短数字串只有紧挨编号上下文才转。
#
# 触发条件（任一）：
#   - 串前一个字符是 '-' 或 ASCII 字母（"BCL零二"、"-八"）；
#   - 串后一个字符是 '-' 或 ASCII 字母或 ASCII 数字（"一零-"）。
#
# 前一个字符是 ASCII **数字**不触发 —— "10-8四通" 的「四」前面是 "8"，
# 它属于后面的物料名（四通），转成 "84通" 就查不到了。
#
# 收尾保护：被转的串若紧跟一个汉字（且该汉字不是数字字），串的最后一个字
# 留着不转 —— "一零杠八四通" 里的「四」属于「四通」，只转到「八」为止。
_CN_DIGIT_RUN_ANY = re.compile(r'[零〇一幺二三四五六七八九]+')
_POSITION_WORDS = '十百千万亿'


def _is_cjk(ch: str) -> bool:
    return bool(ch) and '\u4e00' <= ch <= '\u9fff'


def cn_digits_in_code_context(text: str) -> str:
    """把紧挨编号上下文（连字符/ASCII 字母数字）的短中文数字串转成阿拉伯数字。

    >>> cn_digits_in_code_context('一零-八四通')
    '10-8四通'
    >>> cn_digits_in_code_context('BCL零二')
    'BCL02'
    >>> cn_digits_in_code_context('10-8四通')
    '10-8四通'
    >>> cn_digits_in_code_context('三通')
    '三通'
    """
    if not text:
        return text

    def _sub(m):
        run = m.group()
        prev = text[m.start() - 1] if m.start() > 0 else ''
        nxt = text[m.end()] if m.end() < len(text) else ''
        # 位词相邻的是计量表达（"A-三十五个"），不是编号
        if (prev and prev in _POSITION_WORDS) or (nxt and nxt in _POSITION_WORDS):
            return run
        seeded = bool(re.match(r'[A-Za-z\-]', prev)) or bool(
            re.match(r'[A-Za-z0-9\-]', nxt)
        )
        if not seeded:
            return run
        # 后面紧跟汉字词：最后一个数字字大概率属于那个词（"八四通" → "8四通"）
        if _is_cjk(nxt):
            head, tail = run[:-1], run[-1]
            if not head:
                return run
            return head.translate(_CN_DIGIT_MAP) + tail
        return run.translate(_CN_DIGIT_MAP)

    return _CN_DIGIT_RUN_ANY.sub(_sub, text)


# ── 口播符号词 → 连字符 ──
#
# 用户念编号里的 '-' 有好几种读法，ASR 如实转写成汉字。长词优先，
# 否则 "斜杠" 会被 "杠" 抢先吃掉半个词。
#
# 只在**两侧都是编号字符**（ASCII 字母数字或中文数字字）时才替换：
# "杠"、"减号" 在普通句子里都是常用词（"杠上开花"、"三减号通"、"减号键"），
# 只看一侧会把这些词里的字也换成 '-'，毁掉正常的物料名。
_DASH_WORDS = ('横杠', '斜杠', '破折号', '减号', '杠')
_DASH_WORD_RE = re.compile('|'.join(sorted(_DASH_WORDS, key=len, reverse=True)))
_CODE_CHAR_RE = re.compile(r'[A-Za-z0-9零〇一幺二两三四五六七八九]')


def dash_words_to_hyphen(text: str) -> str:
    """把口播的「横杠/斜杠/破折号/减号/杠」还原成 ``-``。

    >>> dash_words_to_hyphen('一零杠八')
    '一零-八'
    >>> dash_words_to_hyphen('BCL杠02')
    'BCL-02'
    >>> dash_words_to_hyphen('杠上开花')
    '杠上开花'
    >>> dash_words_to_hyphen('三减号通')
    '三减号通'
    """
    if not text:
        return text

    def _sub(m):
        prev = text[m.start() - 1] if m.start() > 0 else ''
        nxt = text[m.end()] if m.end() < len(text) else ''
        if not (_CODE_CHAR_RE.match(prev or '') and _CODE_CHAR_RE.match(nxt or '')):
            return m.group()
        return '-'

    # 只替换符号词本身，不做任何 '-' 的去重/合并 —— 字面值里本来就有的
    # "--"（如 "A--B"）必须原样留着，它是编号的一部分。
    return _DASH_WORD_RE.sub(_sub, text)


# ── 同音/误听词表 ──
#
# ASR 对同音字的选择与声学模型有关，同一台设备上是稳定的错法（"丝通"↔"四通"），
# 但换个行业/口音就是另一套。所以表本身不硬编码在代码里，走配置
# ``config.yml`` 的 ``asr_synonyms``，缺省空表 —— 没配置就完全不生效。
_SYNONYMS: dict = {}


def configure_synonyms(table) -> None:
    """设置进程级默认同音词表（由 MCP 启动时从配置注入）。

    非法项（空键、非字符串）静默丢弃：配置是人手写的，一个笔误不应该
    让整个 MCP 起不来。
    """
    global _SYNONYMS
    clean = {}
    if isinstance(table, dict):
        for k, v in table.items():
            if isinstance(k, str) and isinstance(v, str) and k:
                clean[k] = v
    _SYNONYMS = clean


def get_synonyms() -> dict:
    """返回当前生效的同音词表副本。"""
    return dict(_SYNONYMS)


def apply_synonyms(text: str, table: dict = None) -> str:
    """按同音/误听词表做整词替换（长键优先）。

    ``table`` 为 None 时用 ``configure_synonyms`` 注入的进程级表。

    >>> apply_synonyms('丝通阀', {'丝通': '四通'})
    '四通阀'
    >>> apply_synonyms('四通阀', {'丝通': '四通'})
    '四通阀'
    >>> apply_synonyms('甲乙', {'甲': '乙', '乙': '丙'})
    '乙丙'
    """
    if not text:
        return text
    tbl = _SYNONYMS if table is None else table
    if not tbl:
        return text
    keys = [k for k in tbl if k]
    if not keys:
        return text
    # 一次性扫描替换（长键优先），替换产物不再参与后续匹配：串行
    # ``str.replace`` 会级联（表 {"甲":"乙","乙":"丙"} 把 "甲" 变成 "丙"）。
    pattern = '|'.join(re.escape(k) for k in sorted(keys, key=len, reverse=True))
    return re.sub(pattern, lambda m: tbl[m.group(0)], text)


# ── 剥离提示性前缀 ──

# LLM 常把用户话里的引导词当成名称的一部分传进来。按长词优先排列，
# 避免 "物料编码" 被 "编码" 抢先匹配掉一半。
#
# 只剥开头：句中的 "查询型号100201的库存" 不动 —— 那种整句进来的情况
# 说明 LLM 没有正确提参，剥掉一个词也救不回来，而误剥会伤到真实物料名。
_QUERY_PREFIX_RE = re.compile(
    r'^(?:'
    r'物料编码|产品编码|备件编号|物料编号|产品编号|零件编号|备件号|物料号|零件号|'
    r'型号|规格|编码|编号|货号|料号|'
    r'part\s*no\.?|part\s*number|sku|item\s*no\.?'
    r')'
    r'[\s:：,，、是为的]*',
    re.IGNORECASE,
)


def strip_query_prefix(text: str) -> str:
    """剥离查询词开头的提示性前缀。

    剥完为空则返回原文 —— 万一真有个物料就叫「型号」，剥成空串会让整个
    查询失去意义，保留原文至少还能走模糊匹配。

    >>> strip_query_prefix('型号100201')
    '100201'
    >>> strip_query_prefix('物料编码：ABC-123')
    'ABC-123'
    >>> strip_query_prefix('型号')
    '型号'
    >>> strip_query_prefix('上钳口')
    '上钳口'
    """
    if not text:
        return text
    stripped = _QUERY_PREFIX_RE.sub('', text).strip()
    return stripped or text


# ── 全角 → 半角 ──

# 白名单：只转编号里真正会出现的字符类。
#
# 不能无脑转整个 0xFF01-0xFF5E 全角区 —— 全角括号（U+FF08/09）和全角逗号
# （U+FF0C）也在里面，而物料名里的 "电极帽（银色）" 括号是内容的一部分，
# 转成半角后若 ERP 存的是全角，精确匹配直接断掉。
#
# 连接符 －．／＿ 收进来是因为编号里常见（"ＡＢＣ－１２３"），且它们在
# 名称中作为内容出现的概率远低于括号逗号。
_FULLWIDTH_MAP = {}
for _lo, _hi in ((0xFF10, 0xFF19),   # ０-９
                 (0xFF21, 0xFF3A),   # Ａ-Ｚ
                 (0xFF41, 0xFF5A)):  # ａ-ｚ
    _FULLWIDTH_MAP.update({c: c - 0xFEE0 for c in range(_lo, _hi + 1)})
_FULLWIDTH_MAP.update({
    0xFF0D: 0x2D,  # － → -
    0xFF0E: 0x2E,  # ． → .
    0xFF0F: 0x2F,  # ／ → /
    0xFF3F: 0x5F,  # ＿ → _
    0x3000: 0x20,  # 全角空格 → 半角空格
})


def fullwidth_to_halfwidth(text: str) -> str:
    """全角 ASCII 字符转半角。

    ASR 输出的编号里混全角字符很常见，而 WMS 存的是半角。

    >>> fullwidth_to_halfwidth('ＡＢＣ－１２３')
    'ABC-123'
    """
    if not text:
        return text
    return text.translate(_FULLWIDTH_MAP)


# ── 组合入口 ──

def normalize_query(text, synonyms: dict = None):
    """MCP 工具入口的查询词归一化。

    顺序有讲究：

    1. ``fullwidth_to_halfwidth`` 先做 —— 后面几步的正则按半角写，全角数字和
       冒号得先转半角，前缀正则里的 ``[\\s:：]`` 才吃得到。中文不是全角
       ASCII，"型号" 这类词不受影响。
    2. ``dash_words_to_hyphen`` 次之 —— 先把 "杠" 还原成 ``-``，后面的中文
       数字规则才能把 "一零-八" 认成被连字符切开的编号。
    3. ``cn_digits_to_arabic`` → ``cn_digits_in_code_context`` —— 先转长数字串
       （与 backend/fuzzy_match.py 同规则），再补转被连字符切短的那些。
    4. ``strip_query_prefix`` —— 前缀正则不认中文数字，
       "型号一零零二零一" 得先转成 "型号100201"，剥前缀后才剩 "100201"。
    5. ``apply_synonyms`` 最后 —— 词表写的是最终字面值（"丝通"→"四通"），
       放在数字/符号都归位之后替换才匹配得上。

    非字符串（None、数字等）原样返回，不做类型强转：调用方传什么类型自有
    其道理，静默转换会掩盖上游的错误。

    >>> normalize_query('型号幺零零二零一')
    '100201'
    >>> normalize_query('一零杠八四通')
    '10-8四通'
    >>> normalize_query('上钳口')
    '上钳口'
    >>> normalize_query(None) is None
    True
    """
    if not isinstance(text, str) or not text:
        return text
    out = fullwidth_to_halfwidth(text)
    out = dash_words_to_hyphen(out)
    out = cn_digits_to_arabic(out)
    out = cn_digits_in_code_context(out)
    out = strip_query_prefix(out)
    out = apply_synonyms(out, synonyms)
    return out.strip() or text
