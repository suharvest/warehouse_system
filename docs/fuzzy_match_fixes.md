# 小智仓管方案模糊匹配问题修复记录

## 问题总览

以下问题均来自现场 ASR（语音识别）输入场景，用户通过语音查询仓库物料，ASR 转写结果与数据库物料名称存在差异。

| 查询（语音识别结果） | 正确物料 | Score | Gap | 失败原因 | 分类 | 状态 |
|---|---|---|---|---|---|---|
| 轴承锁套201245 | 轴承锁套，2012-45 | 92.1 | 6.8 | gap < 10 | 阈值过严 | 已修复 |
| 鱼燕轴承FM14150U | 鱼眼轴承（F-M14×150U） | 87.4 | 7.2 | gap < 10 | 阈值过严 | 已修复 |
| 轴承6212Z | 轴承－6212-Z | 88.8 | 5.4 | gap < 10 | 阈值过严 | 已修复 |
| 接近开关e扶门if503a | 接近开关(易福门)-IF503A | 84.6 | 19.8 | score 84.6 < 85 | 阈值过严 | 已修复 |
| 防爆插座潍风32A380V | 防爆插座（惟丰 32A 380V） | 85.0 | 10.3 | score 85 不满足 >85（应为 >=） | 比较符号 bug | 已修复 |
| 轴承6312ZZ | 轴承－6312zz | 81.4 (排第3) | — | 错误候选 轴承-6310-2Z(91分) 排第一 | 匹配算法 bug | 已修复 |
| SKU: C1593011599 | 轴承（33014） | fuzzy 100 | — | product-stats 不支持 SKU 查询，fuzzy 返回的 name 是 SKU 本身 | 接口缺陷 | 已修复 |
| 轴承33013 | 轴承（33013）/ 轴承，33013 | 100 (并列) | 0 | 两条重复物料并列 100 分，confident=false，且低分候选「轴承」(58分)也被返回 | 数据重复 + 噪音候选 | 已修复(噪音过滤) |
| 鱼眼轴承FM16150U | 鱼眼轴承（F-M16×150U） | — | — | 语音识别未正确识别 M16，请求未到达后端 | ASR 端问题 | 非后端问题 |

## 修复详情

### 1. normalize 增强

**问题**：物料名称中的分隔符（逗号、全角横杠、大小写）阻断了包含检查，导致本该 95+ 分的匹配掉到 80-90 分区间。

**修复**：

| 改动 | 说明 | 对应 Case |
|---|---|---|
| 去中文逗号 `,，、` | `轴承锁套，2012-45` → `轴承锁套201245` | 轴承锁套201245 |
| 去全角横杠 `－` 和全角斜杠 `／` | `轴承－6212-Z` → `轴承6212z` | 轴承6212Z |
| 加 `.lower()` 统一大小写 | `6312ZZ` == `6312zz` | 轴承6312ZZ |

**最终 normalize 正则**：
```python
re.sub(r'[\s\-－/／\(\)（）\[\]【】,，、]+', '', text).lower()
```

### 2. 包含匹配分方向计分

**问题**：旧逻辑对 `query⊂name` 和 `name⊂query` 都给 95 分，导致短通用词（如「轴承」）与所有含「轴承」的物料并列 95 分，抢走正确匹配的第一名。

**修复**：
- `query⊂name`（如 `轴承6309` ⊂ `轴承6309AEMC3`）→ **强信号 90-100 分**，按长度比排序
- `name⊂query`（如 `轴承` ⊂ `轴承22062RS`）→ **弱信号 50-80 分**，按长度比降权

```python
if norm_query in norm_name:
    return 90.0 + 10.0 * (len(norm_query) / len(norm_name))
if norm_name in norm_query:
    ratio = len(norm_name) / len(norm_query)
    return 50.0 + 30.0 * ratio
```

### 3. confident 判定逻辑优化

**问题**：多个阈值过严或有 bug，导致明显正确的匹配无法自动确认。

| 改动 | 旧值 | 新值 | 原因 |
|---|---|---|---|
| confident_score | 85 | **80** | 84.6 分的 ASR 品牌错字被拦住 |
| 比较运算符 | `> 85` | `>= 80` | 刚好等于阈值的分数应该通过（app.py 传入的默认值也从 85 改为 80） |
| 梯度 gap | 统一 gap > 10 | score >= 90 时 gap > 5 | 高分匹配不需要那么大的差距 |
| 唯一候选门槛 | 50 分 | **75 分** | 防止弱匹配被错误自动确认 |
| score >= 95 | 需要 gap > 10 | **直接 confident** | 近完全匹配不应被短子串干扰 |
| 并列第一 | 无处理 | **confident=false** | 同分无法区分，交给 LLM 判断 |

**最终 confident 判定逻辑**：
```python
if 并列第一（top2 分数相同）:
    confident = False
elif 唯一候选:
    confident = score >= 75
elif score >= 95:
    confident = True  # 强匹配直接确认
elif score >= 90:
    confident = gap > 5  # 高分放宽 gap
else:
    confident = score >= 80 and gap > 10  # 标准判定
```

### 4. 空查询守卫

**问题**：空字符串或纯空格查询时 `'' in norm_name` 始终为 True，导致所有物料都匹配到 90+ 分。

**修复**：normalize 后为空直接返回空列表。

```python
norm_query = self._normalize(query)
if not norm_query:
    return []
```

### 5. product-stats 支持 SKU 查询

**问题**：用户报 SKU 编号时，精确查询 `WHERE name = ?` 找不到（name 字段不存 SKU）。模糊匹配虽能找到 SKU 索引条目，但返回的 name 字段就是 SKU 本身，再用 SKU 去查 product-stats 还是 404，形成死循环。

**修复**：精确查询同时匹配 name 和 SKU。

```sql
-- 旧
WHERE name = ?

-- 新
WHERE name = ? OR sku = ?
```

### 6. MCP 模糊匹配消息优化

**问题**：confident=false 时返回 `"请指定更精确的名称"`，LLM 被指令绑住不敢自己从候选列表中判断。且候选列表不带分数，LLM 无法区分高分和低分候选。

**修复**：
- 改为引导 LLM 自行判断，并在消息中带上分数：
```
"按相似度排序的候选：轴承（33013）(100分), 轴承，33013(100分)。请根据分数和上下文判断最佳匹配，优先选择高分项，如无法确定再询问用户"
```

### 7. 过滤低分噪音候选

**问题**：查询 `轴承33013` 时，正确匹配 100 分，但低分候选 `轴承`（58.6 分）也被返回，干扰 LLM 判断。

**修复**：与第一名差距超过 20 分的候选直接过滤。

```python
if deduped:
    top_score = deduped[0]["score"]
    deduped = [r for r in deduped if top_score - r["score"] <= 20]
```

## 相关 Commits

```
9d35252 fix: filter low-score noise candidates and show scores in MCP message
4f99a07 fix: app.py confident_score default was 85, overriding fuzzy_match's 80; also use >= instead of >
01b8b5d fix: lower confident_score threshold from 85 to 80
73bc66c fix: case-insensitive matching, SKU search, tie-breaking, LLM guidance
0c49a26 fix: normalize strips full-width dashes; let LLM judge ambiguous matches
8fa4167 fix: normalize strips commas for containment matching
e9a6e43 fix: graduated gap threshold — score>=90 only needs gap>5 for confidence
2ffe8aa fix: guard empty query and raise single-candidate threshold to 75
e21ad2f fix: split containment scoring by direction (query⊂name vs name⊂query)
8487660 fix: score >= 95 auto-confirms, ignoring gap from short substrings
a6842b1 fix: containment match uses length-ratio tiebreaker (90-100 pts)
b9f3fba improve: normalize strips slashes for model number matching
4d4b0be improve: normalize strips brackets for better fuzzy matching
```

## 未修复 / 不需要修复

| 问题 | 判断 | 原因 |
|---|---|---|
| 拼音首字母匹配（zc→轴承） | 不修 | 输入源是 ASR，不会产生拼音 |
| Token 分词匹配 | 不修 | ASR 不会拆分输出，当前包含检查已够用 |
| NFKC Unicode 归一化 | 不修 | ASR 输出纯文本，不会有全角字符 |
| N-gram 预过滤 | 不修 | 当前数据量（百~千级）线性扫描性能足够 |
| 笔画/偏旁相似匹配 | 不修 | 非 OCR 场景 |
| 鱼眼轴承FM16150U | 非后端问题 | ASR 端未正确识别，请求未到达后端 |
