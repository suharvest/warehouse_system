# MCP 提示词与响应系统精简设计（面向 8K 上下文小模型）

> 背景：本地推理引擎专用于驱动 warehouse MCP，上下文上限 ~8K token。当前
> `warehouse_mcp.py` 的 `_RULES_FOOTER`（13 条规则）+ 7 个 tool docstring +
> `_wrap_response` 注入的多字段响应，已经在多轮调用下逼近上限。本文档给出
> 一套精简方案。

---

## 1. 现状盘点（warehouse_mcp.py:265-520）

| 部分 | 大小（粗估 token） | 出现频次 |
|---|---|---|
| `_RULES_FOOTER` 系统提示 | ~750 | 1（initialize 时下发一次） |
| 7 个 tool schemas + docstring | ~700 | 每轮都在 LLM 上下文 |
| 单次 stock_out 成功响应 | ~250-400 | 每次工具调用累计 |
| stock_out 失败带 candidates | ~500+ | 每次工具调用累计 |
| search 含 batches 响应 | 容易 >1000 | 每次工具调用累计 |

**预算压力**：8K 上下文里，系统提示 + schemas 已固定吃 1.5K；再来 2-3 轮
search 类响应（含 batches）就接近爆。

---

## 2. 设计目标

1. 系统提示压到 ≤ 250 token
2. 7 个 tool schema 总和压到 ≤ 300 token
3. 任意单次响应 ≤ 250 token（search 截断后）
4. 把"路由纠错（routing_retry）"等元规则下沉到服务端，LLM 不感知
5. 不牺牲反幻觉：`facts.executed=false` 时 LLM 不能说"已完成"这一硬保障保留

---

## 3. 系统提示：13 条 → 6 条

### 砍掉的规则（原 footer 引用见 warehouse_mcp.py:265-298）

- 第 5 条（candidates 必须让用户选）→ 由响应中 `say_kind=ask` + `say` 文本天然
  约束，规则冗余
- 第 6 条（用户问"现在/最新"必须重查）→ 保留（小模型容易直接复述）
- 第 8 条（side_effect 仅表类型）→ 字段本身删除，规则自动消失
- 第 9 条（缺 speak 字段时拒答）→ schema 改为 `say` 单字段，必填，规则消失
- 第 10 条（"你看着办"也要调工具）→ 通用对话能力，不属于 MCP 规则
- 第 11/12 条（next_action 取值、retry 确认）→ 折叠为新规则 5
- **第 13 条（routing_retry 全机制）→ 完全砍掉**，下沉到 provider

### 新提示词（≈ 180 token）

```text
反幻觉硬规则（六条）：
1. 数字只能来自响应字段，不许口算或推测。
2. say 必须照搬原文，不许改写/合并/增减数字。
3. executed=false 时禁止说"已/完成/成功/出库了/入库了"。
4. say_kind=ask → 念候选、等用户选；say_kind=fail → 仅播报，禁止重试。
5. awaiting_confirm 非空 → 先问用户，用户同意后才用 patch 重发；拒绝则结束。
6. 用户问"现在/还剩/最新"必须重新调工具，不许引用历史结果。
```

通过 `FastMCP(instructions=...)` 在 initialize 阶段一次性下发（保持现有行为，
warehouse_mcp.py:588），不进 tool docstring。

---

## 4. 响应 schema：扁平化到 5 个字段

### 当前问题

一个响应同时携带：
`success / facts.executed / facts.query_at / facts.routing_retry_used /
side_effect / next_action / speak / speak_ask / speak_failed / retry_hint /
error / message / product / batch / candidates / batches / ...`

字段语义重叠严重：`success` ≈ `facts.executed`（写操作时）；
`speak / speak_ask / speak_failed` 三选一；`next_action` 与 `retry_hint` 信息
冗余。小模型在这么多字段间容易选错或漏读。

### 新统一响应

```json
{
  "ok": true,
  "executed": true,
  "say": "已出库螺丝共3个（批次B003出3个），当前库存12个。",
  "say_kind": "tell",
  "data": { ... 业务字段，见 §5 ... },
  "awaiting_confirm": null
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `ok` | bool | 操作是否成功（查询拿到结果 / 写入完成）。替代 `success` |
| `executed` | bool | **数据库是否真改了**。写操作 ok=true 时为 true；查询类永远 false。反幻觉核心闸门 |
| `say` | string | 必填，LLM 必须照搬。所有数字都已嵌入 |
| `say_kind` | enum: `tell` \| `ask` \| `fail` | 决定 LLM 行为：陈述 / 询问候选 / 失败播报 |
| `data` | object | 业务字段（详见 §5），LLM 可忽略，仅供调试与可选 UI |
| `awaiting_confirm` | object \| null | 非空表示"等待用户口头同意后才能用此 patch 重发"。字段名自带语义，LLM 不会误解为"立即重发" |

> **设计要点（Codex 审查修正）**：
> - `ok` 与 `executed` 分离：查询类工具 `ok=true / executed=false`，写操作成功才 `executed=true`。
>   保留原 warehouse_mcp.py:316-318 的核心闸门语义。
> - `awaiting_confirm` 取代 `confirm_patch`：字段名本身表达"等待中"，LLM 不会按"非空就重发"
>   误读，避免 `allow_partial_fallback` 被自动启用造成未授权扣减。

**取消字段**：
- `success` → 合并到 `ok`
- `side_effect` → 由 `executed` + 操作类型隐式表达
- `facts.query_at` → 时间戳放进 `data` 仅必要时
- `facts.routing_retry_used` → routing_retry 机制整体废除
- `speak / speak_ask / speak_failed / next_action` → 折叠为 `say` + `say_kind`
- `retry_hint` → 改名 `confirm_patch`，只保留 patch 本身（删 `reason / tool /
  allowed / requires_user_confirmation` —— 非空即"需要用户同意"）
- `error / message` → 失败时直接进 `say`（LLM 反正只能照搬），原 `error` 仅
  在 `data._error` 里保留供调试

---

## 5. data 区强约束截断

| 工具 | 成功时 data 字段 | 截断/省略规则 |
|---|---|---|
| `query_stock` | `name, qty, unit, batch_count` | 不返回 batches 列表；要明细另调 `query_batch` |
| `query_batch` | `batch_no, name, qty, unit, location` | 单批次只返回当前余量 |
| `search` | `total, items[≤5]`（每条 `name, qty, unit`） | `max_results` 默认从 10 降到 5；不嵌套 batches |
| `stock_in` 成功 | `name, in_qty, after, unit, batch_no` | 删除多余字段 |
| `stock_out` 成功 | `name, out_qty, after, unit, batches: [{no, qty}]` | 多批次合并已嵌入 `say`；`data.batches` 仅 ≤3 条 |
| 任意失败的 `candidates` | `≤3` 条，每条仅 `name` | 删 id/score（小模型用不上） |
| `get_today_statistics` | `in, out, net, total, low` | 5 个数字打平 |

效果：所有响应稳定在 80-250 token 之间。

---

## 6. Tool schema 极简化

### 改动点

1. **每个 tool docstring ≤ 1 行 40 字**，不列 enum 值
2. enum 值仅声明在 JSON schema 的 `enum` 字段（FastMCP 支持），让 provider
   在内部做中文→英文归一化（如"卖出"→`sell`），LLM 直接传中文也可
3. **删除 `routing_retry` 参数**（query_stock / query_batch）
4. **删除 `allow_partial_fallback` 显式参数**，改为客户端拿到 `confirm_patch`
   后由服务端 patch 注入；LLM 无需感知这个布尔
5. 把高级写参数（`contact_id / from_location / product_name 校验`）从必读
   docstring 移到隐藏参数

### 改造后示例

```python
@mcp.tool()
def stock_out(product_name: str, quantity: int,
              reason: str = "sell", batch_no: str = None) -> dict:
    """出库。reason 可用中文或英文。"""

@mcp.tool()
def stock_in(product_name: str, quantity: int,
             reason: str = "purchase") -> dict:
    """入库。reason 可用中文或英文。"""

@mcp.tool()
def query_stock(product_name: str) -> dict:
    """按产品名查库存。"""

@mcp.tool()
def query_batch(batch_no: str) -> dict:
    """按批次号查批次。"""

@mcp.tool()
def search(query: str, kind: str = "material") -> dict:
    """搜索物料/联系方/操作员。kind: material|contact|operator"""

@mcp.tool()
def move_batch(batch_no: str, to_location: str, qty: int = None) -> dict:
    """批次移位；qty 不传则整批挪。"""

@mcp.tool()
def today_stats() -> dict:
    """今日入出库与库存概览。"""
```

7 个工具 schema 总和约 250 token。

---

## 7. 路由纠错下沉到 provider（取消 routing_retry）

### 当前机制（warehouse_mcp.py:290-298 第 13 条）

LLM 自己判断 query_stock / query_batch 哪个失败了，自己换工具重试，必须显式
带 `routing_retry=True`。**对 8K 小模型太复杂**，而且需要 LLM 记住 ≥ 4 条子
规则，违反率高。

### 新方案

在 `DefaultProvider.query_stock` 里：

```python
_BATCH_NO_RE = re.compile(r"^\d{8}-\d+$")  # 实际格式：YYYYMMDD-XXX
                                            # 见 backend/database.py:864-915

def query_stock(self, name, ...):
    resp = self._query_by_name(name)
    if not resp["success"] and not resp.get("candidates"):
        if _BATCH_NO_RE.match(name.strip()):
            batch_resp = self.query_batch(name)
            # 仍失败则回退到原 query_stock 响应，避免双重 "未找到" 混淆用户
            if batch_resp.get("success"):
                return batch_resp
    return resp
```

`query_batch` 也对称做一次反向兜底（输入像产品名时转 query_stock）。LLM
完全不知道发生了切换，响应里也没有 `routing_retry_used` 字段。

代价：服务端要把判定规则维护准确；好处：系统提示 -2 条规则、tool 参数 -1
个、LLM 出错率显著下降。

---

## 8. 兼容性与迁移

### 破坏性变更

1. 字段重命名：`success`→`ok`，`speak*`→`say/say_kind`，`retry_hint`→
   `confirm_patch`
2. 删除字段：`facts.executed`、`side_effect`、`next_action`、
   `facts.routing_retry_used`
3. Tool 参数：删 `routing_retry`、`allow_partial_fallback`、`show_batches`、
   `include_batches`、`variant` 等次要可选参数（保留在 provider 层内部用）

### 迁移策略

- **业务代码（backend/frontend）实测未消费** `speak* / next_action /
  facts.executed` 字段（grep 仅命中注释），改名风险低于初步估计；xiaozhi
  侧仅需读 `say` 文本
- backend 调用 `_provider` 的方法**签名保持不变**，所有精简发生在
  `warehouse_mcp.py` 包装层
- 单元测试 `tests/test_mcp.py` 需要更新字段名断言

### Rollout

1. **第一步（低风险）**：仅改 `_RULES_FOOTER` 和 `_wrap_response`（字段重命
   名 + 截断 data），保留 routing_retry 参数。跑评测验证小模型表现。
2. **第二步**：砍 routing_retry，provider 内部兜底。
3. **第三步**：tool schema 极简化（docstring 一行、删次要参数）。

每步独立可回滚。

---

## 9. 预算核对

| 部分 | 当前 | 新设计 |
|---|---|---|
| 系统提示 | ~750 | ~215（实测 163 中文 / 1.5 + 105 ASCII） |
| 7 个 tool schemas | ~700 | ~250 |
| 单次最大响应（search） | >1000 | ~250 |
| 5 轮对话 + 5 次响应预估 | ~6500 | ~2600 |
| **8K 中剩余余量** | ~1500 | ~5400 |

---

## 10. 风险与取舍

| 取舍 | 收益 | 代价 |
|---|---|---|
| 砍 routing_retry，下沉 provider | 系统提示 -300 token，LLM 不再误判 | provider 需维护批次号正则，误判时用户体验略差 |
| 删 candidates 的 id/score | 响应 -50 token | 后续如要支持"用户选第 N 个"，需用 name 反查（轻微歧义风险） |
| 合并 speak/speak_ask/speak_failed → say + say_kind | 提示规则简化 | xiaozhi 客户端需要适配（仅一处） |
| stock_out 多批次明细只进 say 不进 data.batches | 响应 -100 token | 外部 ERP 调试时少一份结构化明细（log 里仍有） |
| Tool docstring 极简、不列 enum | schema -400 token | LLM 传错 enum 概率上升 → 由 provider 兼容中文别名兜底 |

---

## 11. 开放问题

1. `say` 文本里嵌入了所有数字。若用户后续要"再确认一遍数量"，LLM 只能照
   搬 say，不能从 data 里读 qty 重组——是否可接受？（建议：可接受，因为
   照搬就是反幻觉的目的）
2. `confirm_patch` 非空时，是否需要在 say 里强制包含"请说是或否"的疑问
   尾缀？（建议：是，避免 LLM 漏问）
3. routing_retry 下沉后，正则误判（"螺丝123"被当成批次号）怎么处理？
   （建议：先尝试 query_stock，失败且 candidates 空才转 query_batch，
   且 query_batch 仍失败时返回 query_stock 的原响应而非 query_batch 的）
4. tool 极简化后，多变体物料（variant）怎么消歧？（建议：provider 在
   ambiguous 时返回 candidates，由 LLM 走 say_kind=ask 流程，与现状一致）
