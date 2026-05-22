# 仓管 MCP LLM 评测框架 — 实施 Spec v1

> 目的：评测不同尺寸 LLM + 不同 prompt 在仓管 MCP tool-calling 场景下的表现，辅助选型。
> 范围：端到端真实链路 — Eval Runner → MCP server (stdio) → backend HTTP API → mock sqlite。
> 模型解耦：只接受 OpenAI-compatible `(base_url, api_key, model)`，不耦合模型清单。

---

## 1. 目录结构

```
evals/mcp_llm/
  __init__.py
  SPEC.md                       # 本文件
  run.py                        # CLI 入口 + 调度 (~220 行)
  config.py                     # dataclass: RunConfig / ProviderConfig / CaseFilter (~100)
  backend_proc.py               # spawn/teardown backend uvicorn + sqlite snapshot (~180)
  mcp_client.py                 # spawn MCP server stdio + tool schema dump (~160)
  llm_client.py                 # OpenAI-compat client + stream TTFT 采集 (~140)
  scorer.py                     # TSA/AFA/NF/CR@k/IRR/Latency + pass^k (~300)
  traces.py                     # 失败 case trace + DB snapshot 持久化 (~120)
  report.py                     # markdown/csv/json 输出 (~160)
  cases.schema.json             # 用例 JSONSchema (~180)
  cases/
    seed.yaml                   # 单一 seed file，多 profile
    warehouse_200.jsonl         # 200 用例
  prompts/
    p0_baseline.md
    p1_fewshot.md
    p2_strict_json.md
    p3_simplified.md
```

---

## 2. Backend 启动（关键决策）

**架构**：MCP server 通过 HTTP 调 backend（`mcp/warehouse_mcp.py:83-86` 读 `WAREHOUSE_API_URL` / `WAREHOUSE_API_KEY`，归一化为 `X-API-Key` 见 `mcp/warehouse_mcp.py:90-97`），所以必须起完整 backend。

### 2.1 启动流程（`backend_proc.py`）

每个 eval run 起一个**独立** backend 实例：

```python
# pseudo
def start_backend(seed_db_path: Path, port: int = 12450) -> BackendHandle:
    env = {
        **os.environ,
        "DATABASE_PATH": str(seed_db_path),
        "INIT_MOCK_DATA": "0",
        "ENABLE_AUDIT_LOG": "0",
        "PORT": str(port),
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        ["uv", "run", "python", "backend/app.py"],
        env=env, stdout=PIPE, stderr=PIPE,
    )
    wait_for_http_ok(f"http://127.0.0.1:{port}/health", timeout=15)
    return BackendHandle(proc, port, seed_db_path)
```

- backend 入口：`backend/app.py:6699-6700` 调 `uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get('PORT', 2124)))`，**复用现有 `__main__` 块**，不需要新启动器
- 端口动态分配：`--backend-port-base 12450` + 并发 worker id，避免冲突
- `DATABASE_PATH` 决定 sqlite 文件位置，env 优先级在 backend 内部已就绪（`backend/database.py:51` 读 `DATABASE_PATH`）
- 健康检查：拨 **`/health`**（无 `/api` 前缀，`backend/app.py:172-174` 实际定义），最多 15s 等待

### 2.2 sqlite 初始化与种子（`db.py` 替换为 `backend_proc.py:seed_db()`）

不复用 `tests/conftest.py:73` 因为：
- 评测器是独立进程，不走 pytest fixture
- 但**借鉴**那段逻辑：临时 sqlite 文件 + `INIT_MOCK_DATA=0` + `ENABLE_AUDIT_LOG=0`

流程（**已修正：用 Alembic 而非 `init_database()`，因 backend 启动期跑 Alembic 链，两套 schema 会冲突**）：

1. `tempfile.mkstemp(suffix='.db')` → `db_path`
2. 设 `DATABASE_PATH=db_path` 后 **跑 alembic 建表 + 标记版本**：
   ```bash
   DATABASE_PATH=$db_path uv run alembic -c backend/alembic.ini upgrade head
   ```
   ⚠️ **禁止**用 `database.init_database()`（`backend/database.py:287-307`，legacy DDL，不写 `alembic_version`，backend 启动时 Alembic 会失败）
3. 插入基础种子（直接对 sqlite 执行 SQL，或用 SQLAlchemy Core 走 `backend.metadata`）：
   - `tenants` (id=1, slug='default', name='默认租户')
   - `warehouses` (id=1, slug='default', is_default=1)
   - `system_settings` (key='system_mode', value='self_owned')
   - **API key 正确做法**（`backend/deps.py:209-251` 实际认证逻辑：hash 比对，不看 `is_system`）：
     ```python
     from database import hash_api_key  # backend/database.py:1035-1041
     plain_key = "eval-system-" + secrets.token_hex(16)
     key_hash = hash_api_key(plain_key)
     # INSERT INTO api_keys (name, key_hash, role, tenant_id, warehouse_id, is_disabled, is_system)
     # VALUES ('eval-system', :key_hash, 'operate', 1, NULL, 0, 1)
     ```
     `plain_key` 写入 run config，spawn MCP server 时设 `WAREHOUSE_API_KEY=$plain_key`
4. 应用 `seed.yaml` 中对应 profile（见 §3）
5. **快照**：`shutil.copy(db_path, db_path + ".snapshot")` 作为每个写操作用例的复位基线（文件级 copy，~10ms，远快于 backend 重启）

### 2.3 写操作 case 隔离（**已修正：filesystem snapshot copy，不重启 backend**）

每个 D 类（write_ops）用例独立 sqlite 文件，**通过文件级 copy 复位，避免 backend 重启**：

```python
# 单 backend 实例 + 每 write rep 前 copy snapshot 覆盖 DATABASE_PATH 指向的文件
def reset_write_db(snapshot_path: Path, live_path: Path):
    # 1. 让 backend 进程释放连接（sqlite NullPool 友好，见 backend/db.py:42-47）
    requests.post(f"http://127.0.0.1:{port}/api/_test/drain_pool")  # 见下文
    # 2. 文件 copy
    shutil.copy(snapshot_path, live_path)
    # 3. backend 重新打开连接（NullPool 自动）
```

- ⚠️ `db._reset_snapshot()` **不存在**（spec 旧版误写）。canonical 方案是**文件级 copy + NullPool 友好**
- backend 已用 `NullPool`（`backend/db.py:42-47`），无需池清理；但为安全起见可加测试专用路由 `POST /api/_test/drain_pool`（gated by `EVAL_TEST_MODE=1` env，永不在生产开）
- 成本：每次 copy ~10ms，30 写 case × 5 rep = 150 次 = ~1.5s 总开销（vs 重启 backend 方案 ~5 分钟）
- 退化方案：若 NullPool 仍有 lock 问题，则 backend 重启（成本 ~5 分钟可接受）

读 case（A/B/C/E/F）可**共享** backend 实例并发跑，因为不改 state。

### 2.4 Teardown

`atexit` + signal handler 保证：
- backend Popen.terminate() → wait 5s → kill
- 删除临时 db 文件（除非 `--keep-db-on-fail` 保留失败 case 现场）
- MCP server 子进程同样处理

---

## 3. Seed 数据（单一 seed.yaml + profile）

### 3.1 文件格式

```yaml
profiles:
  base:
    description: "标准库存场景：覆盖 fuzzy 名、SKU、同名变体、低库存、零库存"
    materials:
      - sku: LED-RED-10
        name: 红色LED指示灯
        category: 电气
        unit: 个
        safe_stock: 20
        location: A-01
        batches:
          - batch_no: B-2026-003
            quantity: 2
            initial_quantity: 5
            variant: 红色
            location: A-01
      - sku: BRG-NJ409
        name: 轴承-NJ409MC3
        category: 机械
        unit: 个
        safe_stock: 10
        location: C2-2-07
        stock: 5
      - sku: SCR-M4-100
        name: 螺丝刀
        category: 工具
        unit: 把
        safe_stock: 5
        stock: 0          # 零库存
      # ... 同名歧义：两个"螺丝"产品测候选转述
      - sku: SCR-M3
        name: 螺丝
        category: 五金
        stock: 47
      - sku: SCR-M4
        name: 螺丝
        category: 五金
        stock: 33
    contacts:
      - name: 张三供应商
        is_supplier: true
    operators:
      - name: 小李

  multi_warehouse:
    description: "多仓多变体：测 stock_out batch+location+variant 组合"
    extends: base       # 继承 base 全部条目
    warehouses:
      - id: 2
        slug: secondary
        name: 二号库
    materials: [...]
```

### 3.2 用例引用

每个用例在 JSON 里 `"seed_profile": "base"`，runner 启动 backend 前根据 profile 应用对应种子。

200 用例预期 90%+ 用 `base`，少数复杂 case 用 `multi_warehouse`。

### 3.3 数字真值锚定

`seed.yaml` 里所有数字（stock、quantity、safe_stock）就是 NF scorer 的 ground truth。用例的 `expected.final.numeric_values` 引用 seed 里的值，不要在 case 里重新写硬编码数字 — 维护时只改 seed 一处。

---

## 4. MCP server 启动与连接

### 4.1 启动方式（`mcp_client.py`）

直接 spawn stdio 模式（`mcp/warehouse_mcp.py:885-886` 已有 `mcp.run(transport="stdio")`），**不**复用 `backend/mcp_manager.py` 的 MCPProcessManager（那个走 `mcp_pipe.py` 是给 xiaozhi WebSocket bridge 用的）。

```python
env = {
    "WAREHOUSE_API_URL": f"http://127.0.0.1:{backend_port}/api",
    "WAREHOUSE_API_KEY": system_api_key,   # 来自 §2.2 步骤 3
    "MCP_DEBUG": "0",
}
proc = subprocess.Popen(
    ["uv", "run", "python", "mcp/warehouse_mcp.py"],
    env=env, stdin=PIPE, stdout=PIPE, stderr=PIPE,
)
```

### 4.2 Tool schema 抓取

用官方 `mcp` Python SDK 的 stdio client：
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async with stdio_client(StdioServerParameters(...)) as (r, w):
    async with ClientSession(r, w) as session:
        await session.initialize()
        tools = await session.list_tools()
```

把 tools 转成 OpenAI function schema 格式（name/description/parameters JSON Schema），缓存在 RunConfig 里。

### 4.3 LLM ↔ MCP 桥接（**多轮明确：live LLM per turn，不预录 assistant**）

每个用例**单一对话上下文**贯穿所有 `user_turns`：

```python
messages = [{"role": "system", "content": prompt_text}]
for user_turn in case.user_turns:
    messages.append({"role": "user", "content": user_turn})
    # 内层 tool-calling 循环（直到 LLM 不再要 tool）
    while True:
        resp = llm.chat.completions.create(messages, tools, tool_choice="auto", stream=True)
        # collect tool_calls + content
        if resp.tool_calls:
            messages.append({"role": "assistant", "tool_calls": [...]})
            for tc in resp.tool_calls:
                tool_result = await mcp_session.call_tool(tc.name, tc.args)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(tool_result)})
        else:
            messages.append({"role": "assistant", "content": resp.content})
            break  # 进入下一 user_turn
    # 记录本轮的 tool_calls + final assistant message 供 scorer
```

- 用例 schema 里**只有 `user_turns`**，没有 `assistant_turns`，因为 assistant 回复是评测目标，必须实时 LLM 产出
- anti-hallucination contract 把 `speak/speak_ask/speak_failed` 放在 tool result 里（`mcp/warehouse_mcp.py:514-519`），LLM 应该照搬 — 这是 NF scorer 的关键 anchor

### 4.4 错误处理与失败分类

| 失败类型 | 触发条件 | scorer 处理 |
|---|---|---|
| `infra_failure` | MCP 传输异常 / backend 5xx / LLM API 异常 | **跳过该 rep**，不计入 pass^k 分母，retry 1 次 |
| `tool_returned_failure` | tool result 含 `facts.executed=false` 或 `success=false`（`mcp/warehouse_mcp.py:616-621`） | 记录为正常结果，scorer 按 `expected.tool_calls` 是否预期此失败评判 |
| `no_tool_when_required` | LLM 未调任何 tool 但 `expected.no_tool=false` | TSA=0、NF 检查（若编造数字直接 fail）、标 `tag:no_tool_when_required` |
| `wrong_tool_called` | 调了非预期工具 | TSA=0，但 AFA 仍按实际 tool 评估（找最相似 expected）|
| `extra_tools_called` | 在预期之外多调了 tool（非 routing_retry 白名单） | TSA 部分扣分（额外调用每个 -10%）|

---

## 5. 用例 JSON Schema（cases.schema.json）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["id", "class", "seed_profile", "user_turns", "expected"],
  "properties": {
    "id": {"type": "string", "pattern": "^[A-F]-\\d{3}$"},
    "class": {
      "enum": ["A_fuzzy_name", "B_sku_batch", "C_multi_constraint",
               "D_write_ops", "E_irrelevance", "F_numeric_trap"]
    },
    "seed_profile": {"type": "string"},
    "user_turns": {
      "type": "array",
      "items": {"type": "string"},
      "minItems": 1
    },
    "expected": {
      "type": "object",
      "required": ["tool_calls", "final"],
      "properties": {
        "tool_calls": {
          "type": "array",
          "description": "顺序敏感；空数组+no_tool=true 表示该拒答",
          "items": {
            "type": "object",
            "required": ["name", "args"],
            "properties": {
              "name": {"type": "string"},
              "args": {"type": "object"},
              "args_match": {"enum": ["exact", "subset", "regex"], "default": "subset"},
              "allow_routing_retry": {"type": "boolean", "default": false}
            }
          }
        },
        "no_tool": {"type": "boolean", "default": false},
        "final": {
          "type": "object",
          "properties": {
            "anchor_speak": {"type": "boolean", "default": true,
              "description": "true 时要求 final 文本包含 tool 返回的 speak/speak_ask/speak_failed 之一"},
            "must_contain": {"type": "array", "items": {"type": "string"}},
            "must_not_contain": {"type": "array", "items": {"type": "string"}},
            "must_not_match": {"type": "array", "items": {"type": "string"},
              "description": "regex 黑名单：/约|大概|差不多|左右/、/\\d+(00|0)\\b/"},
            "numeric_values": {"type": "array",
              "description": "ground truth 数字（字符级精确）"}
          }
        },
        "db_assertions": {
          "type": "array",
          "items": {"type": "object",
            "required": ["table", "where", "expect"],
            "properties": {
              "table": {"type": "string"},
              "where": {"type": "object"},
              "expect": {"type": "object"}
            }
          }
        }
      }
    },
    "pass_k_overrides": {
      "type": "object",
      "properties": {"k": {"type": "integer"}, "temperature": {"type": "number"}}
    }
  }
}
```

### 5.1 三个种子用例示范

**A-001（模糊名）**：
```json
{
  "id": "A-001",
  "class": "A_fuzzy_name",
  "seed_profile": "base",
  "user_turns": ["螺丝叨还剩几个"],
  "expected": {
    "tool_calls": [
      {"name": "query_stock", "args": {"product_name": "螺丝刀"}, "args_match": "subset"}
    ],
    "final": {
      "anchor_speak": true,
      "must_contain": ["0", "螺丝刀"],
      "must_not_match": ["大概", "差不多"]
    }
  }
}
```

**D-001（写操作 + DB 断言）**：
```json
{
  "id": "D-001",
  "class": "D_write_ops",
  "seed_profile": "base",
  "user_turns": ["把 B-2026-003 这批出库 3 个，原因领用"],
  "expected": {
    "tool_calls": [
      {"name": "stock_out",
       "args": {"batch_no": "B-2026-003", "quantity": 3, "reason_category": "use"},
       "args_match": "subset"}
    ],
    "final": {
      "anchor_speak": true,
      "must_not_contain": ["已成功出库 3"]
    },
    "db_assertions": [
      {"table": "batches", "where": {"batch_no": "B-2026-003"},
       "expect": {"quantity": 2}}
    ]
  }
}
```

**F-001（数字陷阱 + 候选转述）**：
```json
{
  "id": "F-001",
  "class": "F_numeric_trap",
  "seed_profile": "base",
  "user_turns": ["螺丝还剩多少"],
  "expected": {
    "tool_calls": [
      {"name": "query_stock", "args": {"product_name": "螺丝"}, "args_match": "subset"}
    ],
    "final": {
      "anchor_speak": true,
      "must_contain": ["47", "33"],
      "must_not_match": ["大概", "约", "\\d+(00|0)\\b"]
    }
  }
}
```

---

## 6. Scorer 算法（scorer.py）

### 6.1 TSA — Tool Selection Accuracy
- 抽 actual tool_call 名字序列，与 `expected.tool_calls[].name` 比较
- **顺序敏感**主分，无序匹配作为备份信息（不影响 TSA pass）
- 多轮中每轮独立计分，case 最终 TSA = avg(turn_TSA)
- 允许 `allow_routing_retry=true` 时插入一次额外 tool call（参考 `mcp/warehouse_mcp.py:290-297` 的 routing_retry 规则）

### 6.2 AFA — Argument Fill Accuracy
- 递归比较 args 字典，按 `args_match` 模式：
  - `exact`：键值完全一致
  - `subset`（默认）：expected 的所有键值在 actual 中存在即可，actual 可多
  - `regex`：value 是 regex pattern
- 数字字符串归一化：仅当目标 schema 是 `integer/number` 时
- 输出 JSON Pointer 路径定位失配，便于失败 trace

### 6.3 NF — Numeric Fidelity（最严格）

**白名单**：递归遍历**所有** tool result（JSON 树深度遍历），抽出所有 leaf 数值，构建 `allowed_numbers: set[str]`。覆盖的字段路径（glob）：
- `/facts/**`
- `/product/**` `/batch/**`
- `/batches/*/**`（query_stock 的批次数组）
- `/batch_consumptions/*/**`（stock_out，见 `mcp/warehouse_mcp.py:331-336`）
- `/items/*/**` `/items/*/batches/*/**`（search 嵌套，`mcp/warehouse_mcp.py:801-803`）
- `/source_batch/**` `/target_batch/**`（move_batch_location）
- `/today_in`, `/today_out`, `/net_change`, `/total_stock`, `/low_stock_count`（get_today_statistics）

实现：
```python
def collect_numbers(obj, out: set):
    if isinstance(obj, (int, float)): out.add(str(obj))
    elif isinstance(obj, str):
        for m in re.findall(r'\d+(?:\.\d+)?', obj): out.add(m)
    elif isinstance(obj, dict):
        for v in obj.values(): collect_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj: collect_numbers(v, out)
```

抽 final 文本所有数字 token（regex `(?<![A-Z])\d+(?:\.\d+)?(?![A-Z])`），与白名单 + `expected.final.numeric_values` 比对。

**任一规则违反即 fail**：
- 出现白名单外的数字 → hallucinated
- `expected.numeric_values` 中数字未出现 → missing
- `must_not_match` 命中（`大概|约|差不多|\d+(00|0)\b`） → rounded

speak anchor：若 `anchor_speak=true` 且 final 不包含 tool 返回的 `speak/speak_ask/speak_failed` 任一字段子串（≥30% Levenshtein 相似度），降级警告而非 fail（防过严）

### 6.4 CR@k — Candidate Recall
- 触发条件：tool 返回 `next_action=ask_user_to_choose`（**验证存在**：`mcp/warehouse_mcp.py:345,393,413,442`）或顶层 `candidates[]` 非空
- 候选归一化：取每个 `candidate.name` 作为必出现的 token；若有 SKU/batch_no 也要求出现（按 `candidate` 结构动态判断哪些字段非空）
- 通过条件：final 文本包含 candidates 列表中**全部** name（substring），SKU/batch_no 若存在则也必须出现
- 比对范围：默认全候选列表；若候选超过 5 个，按 top-5 计 CR@5（spec 不要求模型列 >5 候选）
- 失败模式：只列第一个、列了 candidates 之外的、隐藏候选直接挑一个

### 6.5 IRR — Irrelevance Reject Rate
- 仅 E 类用例
- 通过条件：`tool_calls == []` 且 final 包含拒答语义（"无法|不能|抱歉|不在我职责|我只负责" 之一）且无任何编造数字
- 写操作工具被调用直接 -2 分（重罚）

### 6.6 TS-pass^k
- k=5 默认，每个 rep 用不同 seed（`base_seed + rep * 1000`），temperature=0.3
- case_pass = 全部 5 次 (TSA + AFA + NF + 类特定指标) 都过
- 报告同时输出 pass^1 (任一通过) 和 pass^5 (全通过)，便于看稳定性 gap

### 6.7 Latency
- LLM 客户端 `stream=True` + 自定义计时：
  - `t_request_start`：调用 create 前
  - `t_first_token`：**跳过 role-only chunk**（OpenAI 流首 chunk 一般是 `delta={"role":"assistant"}`，content 为空），等第一个 `delta.content` 非空字符串 **或** `delta.tool_calls` 出现非空 `id`/`function.name`/`function.arguments` 片段
  - `t_complete`：stream 结束
- 报 TTFT = t_first_token - t_request_start，e2e = t_complete - t_request_start
- 多轮 case 报每轮 latency + 总和
- 输出 per-model × per-class p50 / p95

---

## 7. Runner 调度（run.py）

### 7.1 生命周期
```
1. 解析 CLI + 加载 cases + prompts + seed.yaml
2. 为每个 seed_profile 生成 sqlite snapshot（一次性）
3. 起 backend (port=12450)，等 health ok
4. 起 MCP server（连 backend）
5. 通过 mcp client list_tools → 转 OpenAI tool schema
6. 并发跑读类 case（asyncio + Semaphore=concurrency）
7. 串行跑写类 case：每 case 复位 sqlite → 重启 backend → 跑 → 记 DB assertion
8. 收集结果 → 写 reports/
9. teardown 全部子进程 + 清临时文件
```

### 7.2 并发与隔离
- 读 case：`--concurrency 4`（默认），共享一个 backend
- 写 case：串行；可选 `--write-concurrency 2`（要求每个 worker 独立 backend 端口）
- pass^k 内部串行（k=5 在同一 case 上跑 5 次），不同 case 之间走 §7.1 的并发

### 7.3 失败 trace
保留：
- 完整 LLM messages（含 tool_calls / tool results）
- MCP raw response（含 `facts` / `speak` 字段）
- scorer 详细输出（哪条规则 fail）
- 写操作 case：失败时保留 `case_<id>_rep<k>.db` 文件供事后 sqlite 查询
- prompt + 用例文件指针

### 7.4 输出结构
```
reports/<run_id>/
  run_config.json         # 完整 CLI 参数 + git sha + 模型/prompt 配置
  summary.md              # 按 class × prompt 的 6 指标表 + pass^5 排名
  metrics.csv             # 扁平化所有 (case, prompt, rep, metric) 行
  metrics.json            # 嵌套结构原始数据
  latency.csv             # per-call TTFT/e2e
  failures/
    A-001__p1_fewshot__rep3.json
    D-007__p0_baseline__rep1.json
    D-007__p0_baseline__rep1.db
```

### 7.5 报告示例（summary.md）

```markdown
# Eval Run abc123 — qwen2.5-7b @ vllm.local

| Class | Prompt | TSA | AFA | NF | CR@k | IRR | pass^5 | TTFT p95 |
|---|---|---|---|---|---|---|---|---|
| A_fuzzy_name | p0 | 92.5% | 80.0% | 95.0% | — | — | 67.5% | 0.42s |
| A_fuzzy_name | p1 | 97.5% | 92.5% | 97.5% | — | — | 87.5% | 0.45s |
| ...
```

---

## 8. CLI 接口

```bash
uv run python -m evals.mcp_llm.run \
  --base-url http://localhost:8000/v1 \
  --api-key sk-xxx \
  --model qwen2.5-7b-instruct \
  --cases evals/mcp_llm/cases/warehouse_200.jsonl \
  --seed evals/mcp_llm/cases/seed.yaml \
  --prompt evals/mcp_llm/prompts/p1_fewshot.md \
  --k 5 \
  --concurrency 4 \
  --output reports/qwen2.5-7b/
```

**必填**：`--base-url --api-key --model --cases --prompt`
**可选默认**：
| 参数 | 默认 | 说明 |
|---|---|---|
| `--seed` | `evals/mcp_llm/cases/seed.yaml` | seed file |
| `--k` | `5` | pass^k 次数 |
| `--temperature` | `0.3` | sampling |
| `--concurrency` | `4` | 读 case 并发 |
| `--write-concurrency` | `1` | 写 case 并发 |
| `--timeout` | `60` | 单次 LLM call 超时 (s) |
| `--tool-choice` | `auto` | LLM tool_choice |
| `--backend-port-base` | `12450` | 端口起点 |
| `--mcp-script` | `mcp/warehouse_mcp.py` | MCP server 入口 |
| `--output` | `reports/<timestamp>` | 报告目录 |
| `--stream` | `true` | 用 stream 取 TTFT |
| `--keep-db-on-fail` | `false` | 保留失败 case 的 sqlite |
| `--filter-class` | — | 只跑指定 class（逗号分隔） |
| `--filter-id` | — | 只跑指定 case id |
| `--limit` | — | 限制用例数 |
| `--fail-fast` | `false` | 首个 fail 即停 |
| `--dry-run` | `false` | 校验配置不跑 |

### 矩阵模式（跑多 prompt）

```bash
uv run python -m evals.mcp_llm.run \
  --base-url ... --api-key ... --model qwen2.5-14b \
  --cases ... \
  --prompt-dir evals/mcp_llm/prompts/ \    # 替代 --prompt，跑全部 .md 文件
  --output reports/qwen2.5-14b/
```

---

## 9. Prompt A/B 矩阵

### p0_baseline.md
- 仅角色 + 工具列表 + "用工具查仓库数据"
- 不含 few-shot、不含数字铁律
- 目的：测大模型原生能力，作为对照基线

### p1_fewshot.md（推荐 7B-14B）
- 完整数字铁律（用户给的版本）
- 6 个 few-shot 示例：
  1. 模糊名命中
  2. 批次号查询
  3. 多候选转述
  4. 零库存 / 查不到
  5. 写操作 partial_fallback 确认
  6. 无关问题拒答
- 强调"照搬 speak 字段"

### p2_strict_json.md（推荐 14B+）
- 要求模型内部先输出 `{intent, tool, args}` 结构化思考再调用
- 强调 `tool_choice="required"` 配合 xgrammar / guided_json
- 适合能 follow structured planning 的大模型

### p3_simplified.md（推荐 ≤7B）
- 删冗余，只留 3 句：
  1. 仓管助手身份
  2. 数字必须原样
  3. 调工具看 `next_action`，照搬对应 speak 字段
- 测小模型在最少干扰下的表现

### 评测矩阵建议
- 首轮：每个模型 × 4 prompt 全跑（200 case × 4 prompt × 5 rep = 4000 次/模型）
- 选出每模型最佳 prompt → 第二轮深入对比

胜出标准：`pass^5` > `NF` > `latency p95` > 综合主观可读性

---

## 10. 依赖清单（pyproject.toml 新增）

```toml
[project.optional-dependencies]
eval = [
  "openai>=1.50",
  "mcp>=1.0",
  "anyio",
  "rapidfuzz",          # Levenshtein
  "pyyaml",
  "rich",               # 控制台进度
  "jsonschema",
]
```

安装：`uv sync --extra eval`

---

## 11. 风险与回退

| 风险 | 缓解 |
|---|---|
| backend 启动慢拖累 D 类用例 | 池化：保持 2-3 个备用 backend 进程，复位时 swap DB 文件 + restart 单进程 |
| 中文 LLM streaming TTFT 不稳 | 多次重测取中位数；用真实 `usage.completion_tokens / elapsed` 算 TPS |
| `speak` anchor 太严 | 默认 Levenshtein 相似度 ≥30%，可调；带 anti-hallucination contract 的模型应 ≥80% |
| 跑大矩阵成本高 | 默认 `--limit 50` 做冒烟，确认无问题再跑全集 |
| seed.yaml 越来越大 | 早期分 profile 隔离，避免 base 膨胀；用 `extends` 减少重复 |

---

## 12. 实施分阶段

**Phase 1 — MVP**（实施 agent 一次性产出）
- §1 全部目录 + 空文件
- §2 backend_proc.py 完整
- §3 seed.yaml `base` profile + 20 条种子用例（A×5/B×3/C×3/D×3/E×3/F×3）
- §4 mcp_client.py 完整
- §5 cases.schema.json
- §6 scorer.py 实现 TSA / AFA / NF（不含 CR@k / IRR 暂用 stub）
- §7 run.py 基础调度（读类并发 + 写类串行，pass^k=1）
- §8 CLI 必填参数 + 默认值
- §9 p0 + p1 两份 prompt
- 验收：用一个云端 OpenAI-compat endpoint 跑通，输出 markdown 报告

**Phase 2 — 完整化**
- 用例扩到 200
- pass^5 + seed 轮换
- CR@k / IRR scorer
- p2 / p3 prompt
- 矩阵模式

**Phase 3 — 上线**
- backend 池化优化
- 跑 8 模型 × 4 prompt 全矩阵
- 出选型报告

---

## Appendix A — 关键代码引用

- MCP server FastMCP 初始化：`mcp/warehouse_mcp.py:553`
- 反幻觉规则：`mcp/warehouse_mcp.py:265-298`
- API auth 归一化：`mcp/warehouse_mcp.py:83-97`
- `_wrap_response` 注入 speak：`mcp/warehouse_mcp.py:304-520`
- `_WRITE_OPS` 集合：`mcp/warehouse_mcp.py:300-301`
- MCP stdio 入口：`mcp/warehouse_mcp.py:885-886`
- Backend uvicorn 入口：`backend/app.py:6699-6700`
- DATABASE_PATH 读取：`backend/database.py:51`
- `api_keys` 表 schema：`backend/database.py:423` (含 `is_system` 列)
- tests/conftest.py sqlite + INIT_MOCK_DATA 模式：`tests/conftest.py:73-90`（仅参考，不直接复用）
- API key hash：`backend/database.py:1035-1041` (`hash_api_key()`)
- API key 认证逻辑：`backend/deps.py:209-251`（hash 比对，不看 `is_system`）
- Health endpoint：`backend/app.py:172-174` (`@app.get("/health")`)
- Alembic 链：`backend/alembic/versions/`（启动时 `backend/app.py:6460-6475` 自动跑 upgrade）
- `_test/drain_pool` 测试路由：**尚未实现**，需在 §12 Phase 1 创建（gated by `EVAL_TEST_MODE=1`）

## Appendix B — Codex Review v1 已修正项

- [BLOCKER] API key bootstrap：必须 `hash_api_key` 后写 `key_hash`，不能存明文（§2.2 步骤 3 已修正）
- [BLOCKER] DB 初始化：必须用 `alembic upgrade head`，不能用 `init_database()`（§2.2 步骤 2 已修正）
- [BLOCKER] 写 case 复位：文件级 snapshot copy，不重启 backend（§2.3 已修正）
- [MEDIUM] Health URL: `/health` not `/api/health`（§2.1 已修正）
- [MEDIUM] NF 嵌套递归 + glob 白名单（§6.3 已修正）
- [MEDIUM] CR@k 候选归一化用 `candidate.name`（§6.4 已修正）
- [MEDIUM] 多轮 live LLM per turn 明确（§4.3 已修正）
- [MEDIUM] MCP 异常分类表（§4.4 新增）
- [LOW] TTFT 跳过 role-only chunk（§6.7 已修正）
- 现有契约测试：`tests/contracts/mcp/*.json`
- backend/mcp_manager.py（**不复用**，仅参考 env 设置惯例）：`backend/mcp_manager.py:103-110`
