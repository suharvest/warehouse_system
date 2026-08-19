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

def normalize_query(text):
    """MCP 工具入口的查询词归一化。

    顺序有讲究：

    1. ``fullwidth_to_halfwidth`` 先做 —— 后两步的正则按半角写，全角数字和
       冒号得先转半角，前缀正则里的 ``[\\s:：]`` 才吃得到。中文不是全角
       ASCII，"型号" 这类词不受影响。
    2. ``cn_digits_to_arabic`` 次之 —— 前缀正则不认中文数字，
       "型号一零零二零一" 先转成 "型号100201"，剥前缀后才剩 "100201"。
       顺序反过来虽然也能剥掉前缀，但下游拿到的还是中文数字，白做。
    3. ``strip_query_prefix`` 最后 —— 前两步不改变前缀词本身，放最后剥一次即可。

    非字符串（None、数字等）原样返回，不做类型强转：调用方传什么类型自有
    其道理，静默转换会掩盖上游的错误。

    >>> normalize_query('型号幺零零二零一')
    '100201'
    >>> normalize_query('上钳口')
    '上钳口'
    >>> normalize_query(None) is None
    True
    """
    if not isinstance(text, str) or not text:
        return text
    out = fullwidth_to_halfwidth(text)
    out = cn_digits_to_arabic(out)
    out = strip_query_prefix(out)
    return out.strip() or text
