# MCP 集成指南

[English](README_EN.md) | 中文

本目录是仓库系统的 **MCP 接入层**。它做两件事：

1. 把仓库业务能力（查库存 / 出入库 / 搜索 / 批次 / 统计）暴露成 MCP 工具，供语音设备（SenseCAP Watcher + 小智）或任意 MCP Host（Claude Desktop 等）调用；
2. 通过 **Provider 抽象层**把这些工具的后端换成任意第三方 WMS/ERP，工具层代码零改动。

因此对外集成有两条路径，先按下表选路：

| 你的情况 | 走哪条路 | 你要写什么 | 主文档 |
|---|---|---|---|
| **已有自己的业务系统**（WMS / CRM / ERP / 自研后端），想让它被语音/AI 调用 | **路径 A：把你的系统封装成 MCP** | 一个 Provider 子类（或一个自己的 FastMCP server） | 本文 §2 + [MCP_External_System_Integration.md](../docs/MCP_External_System_Integration.md) |
| **用的是别人的系统**（第三方 WMS/ERP/SaaS），想让它接进我们这套仓库系统 + 设备 | **路径 B：写设备桥接 Provider 上传给我们** | 一个 Provider `.py` 文件，从 Web UI 上传 | 本文 §3 + [WMS_Provider_Development.md](../docs/WMS_Provider_Development.md) |

两条路径共用同一个 Provider 接口，区别只在**这个 Provider 由谁运行**：路径 A 你自己部署 MCP 进程；路径 B 你把文件交给我们的系统，由我们的后端校验、托管、拉起。

---

## 1. 组件地图

```
语音设备 / MCP Host
        │  (WebSocket wss://  或  stdio)
        ▼
   mcp_pipe.py            ← WS ↔ stdio 管道，断线重连、协议日志
        │  stdio (JSON-RPC)
        ▼
   warehouse_mcp.py       ← MCP 工具层（8 个 @mcp.tool），人脸权限守卫、反幻觉包装
        │  Provider 接口（6 必需 + 2 可选方法）
        ▼
   providers/
     ├── base.py          ← BaseProvider：HTTP helper + 认证 + 抽象方法
     ├── default.py       ← 对接本仓库自带 FastAPI 后端
     ├── validator.py     ← 上传文件的 AST 安全扫描 + 结构校验
     ├── test_runner.py   ← Level 1 / Level 2 连通性测试
     └── custom/          ← 用户上传的第三方 Provider（自动发现）
```

| 文件 | 作用 |
|---|---|
| `warehouse_mcp.py` | MCP 工具定义与响应整形，**不直接碰任何后端 HTTP** |
| `mcp_pipe.py` | 把 stdio MCP server 桥到 `wss://` 端点；带指数退避重连与 JSON-RPC 事件日志 |
| `start_mcp.sh` / `start_mcp.ps1` | 本地手动启动（检查 uv、探活后端、拉起 pipe） |
| `config.yml.example` | 配置模板：`provider` / `api_base_url` / `auth` / `timeout` |
| `providers/` | Provider 注册表，自动扫描本目录与 `custom/` 子目录 |

**当前工具集**（以 `warehouse_mcp.py` 的 `@mcp.tool()` 为准）：

| 工具 | 用途 |
|---|---|
| `resolve_name(text, entity_type)` | 模糊文本 → 精确实体名（语音识别结果消歧） |
| `query_stock(product_name)` | 查库存 |
| `query_batch(batch_no)` | 查批次 |
| `stock_in(product_name, quantity, ...)` | 入库 |
| `stock_out(product_name, quantity, ...)` | 出库 |
| `search(query, entity_type, ...)` | 统一搜索（物料/联系方/操作员） |
| `move_batch_location(batch_no, new_location, ...)` | 批次移库 |
| `get_today_statistics()` | 当天统计 |

字段级返回示例见 [MCP_README.md](MCP_README.md)。

---

## 2. 路径 A：把你自己的系统封装成 MCP，接到我们的设备

适用于「我有系统，我要语音控制它」。有两种做法，**优先选 A1**。

### A1（推荐）：只写一个 Provider，复用整套 bridge

你复用 `mcp_pipe.py` + `warehouse_mcp.py` + 工具描述（这些描述已针对语音场景调过：消歧、候选播报、数量确认、反幻觉），只把数据源换成你的 API。

**1）新建 Provider**

在 `mcp/providers/` 下建 `my_wms.py`：

```python
from .base import BaseProvider


class MyWmsProvider(BaseProvider):
    PROVIDER_NAME = "my_wms"          # 对应 config.yml 的 provider 字段

    def __init__(self, config: dict):
        super().__init__(config)
        self.warehouse_id = config.get("warehouse_id", "")   # 自定义配置字段

    def resolve_name(self, text, entity_type="all"): ...
    def query_stock(self, product_name, show_batches=False): ...
    def stock_in(self, product_name, quantity, reason_category, reason_note,
                 operator, fuzzy, location=None, contact_id=None,
                 variant=None, allow_new_variant=False, actual_operator=None): ...
    def stock_out(self, product_name, quantity, reason_category, reason_note,
                  operator, fuzzy, variant=None, location=None, batch_no=None,
                  location_fuzzy=False, allow_partial_fallback=False,
                  actual_operator=None): ...
    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0): ...
    def get_today_statistics(self): ...

    # 可选（不实现则对应工具恒返回 not_implemented）
    def query_batch(self, batch_no): ...
    def move_batch_location(self, batch_no, new_location, quantity=None,
                            from_location=None, product_name=None,
                            operator="MCP系统"): ...
```

> ⚠️ **签名以 `providers/base.py` 为准，参数顺序不能改**——工具层和连通性测试都按位置传参。逐参数语义、返回格式契约、以及一个可直接复制的完整示例见 [WMS_Provider_Development.md](../docs/WMS_Provider_Development.md)。

基类已提供 `http_get()` / `http_post()`，自动带认证头并把异常收敛成 `{"error": ...}`；认证方式（`api_key` / `bearer` / `basic` / `custom`）在 `config.yml` 的 `auth` 块声明，不需要自己拼 header。

`actual_operator` 是人脸识别真正认出来的人名（见 §2.5），与 LLM 填的 `operator` 分开记账；不做人脸时恒为 `None`。

**2）配置**

```bash
cd mcp && cp config.yml.example config.yml
```

```yaml
provider: "my_wms"
api_base_url: "https://your-wms.example.com/api/v1"
auth:
  type: bearer
  token: "your-token"
timeout: 15
warehouse_id: "WH-001"     # 任意自定义字段，原样传给 Provider
```

环境变量优先级更高：`WAREHOUSE_API_URL` / `WAREHOUSE_API_KEY` / `WAREHOUSE_PROVIDER`。

**3）启动**

```bash
export MCP_ENDPOINT="wss://<你的 MCP 端点>"
./start_mcp.sh
```

日志出现 `使用 provider: my_wms (MyWmsProvider)` 即切换成功。

也可以不用 WS 端点、直接挂到 MCP Host（stdio）：

```json
{
  "mcpServers": {
    "warehouse-system": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "warehouse_mcp.py"],
      "cwd": "/absolute/path/to/warehouse_system/mcp"
    }
  }
}
```

详见 [docs/CLAUDE_DESKTOP_CONFIG.md](../docs/CLAUDE_DESKTOP_CONFIG.md)。

**4）交给我们的后端托管（可选）**

不想自己守护进程的话，把 MCP 端点填到 Web UI 的 MCP 连接管理里，由后端 `mcp_manager` 负责拉起、重连、日志与设备绑定：

| 接口 | 用途 |
|---|---|
| `POST /api/mcp/connections` | 新建连接（端点 + API Key） |
| `POST /api/mcp/connections/{id}/start` \| `/stop` \| `/restart` | 生命周期 |
| `GET /api/mcp/connections/{id}/logs` | 拉协议日志排障 |
| `GET/POST /api/mcp/connections/{id}/devices` | 绑定设备到该连接 |

### 2.5 人脸识别闸门（走 A1 必读）

**人脸闸门在工具层，不在 Provider 层。** `warehouse_mcp.py` 里 8 个工具**全部**会先过 `_enforce_face()`（写操作用各自的 operation，`search` / `query_stock` / `query_batch` / `get_today_statistics` / `resolve_name` 用 `operation="query"`），然后才调你的 Provider。也就是说：**A1 会无条件继承人脸闸门，你换 Provider 换不掉它。**

闸门的实现是一句 HTTP 调用：

```
POST {api_base_url}/face/verify-mcp
→ {"status": "pass" | "skipped" | "deny", "failure_reason", "confidence",
   "matched_subject_id", "matched_subject_name"}
```

`pass` / `skipped` 放行，`deny` 阻断。**策略是 fail-closed**：宁可挡住也不静默放过。

#### ⚠️ 坑在这里：闸门和 Provider 共用同一个 `api_base_url`

`_face_guard()` 用的是 `config['api_base_url']`，而 `BaseProvider.__init__` 也把同一个字段读成 `self.base_url`。所以你一旦把 `api_base_url` 改成自己的 WMS，闸门就会去你的 WMS 上找 `/face/verify-mcp` —— 找不到就是 `deny`，**全部 8 个工具（包括纯查询）直接瘫掉**。

实测确认（本地探针，非推断）：

| `api_base_url` 指向 | `_face_guard()` 返回 | 后果 |
|---|---|---|
| 你的 WMS，无 `/face/verify-mcp`（404） | `{"status": "deny", "failure_reason": "http_404"}` | **8 个工具全阻断** |
| 不可达的地址 | `{"status": "deny", "failure_reason": "transport_error"}` | **8 个工具全阻断** |
| 空字符串 | `{"status": "skipped", "failure_reason": "no_api_base"}` | 放行，但 Provider 也失去 base_url |
| 我们的后端 | 由后端按租户规则判定 | 正常 |

#### 三种解法

**方案 1（推荐）：`api_base_url` 继续指我们的后端，Provider 自己用另一个字段**

闸门保持能用（人脸功能可开可关，由我们后端的规则决定），你的 Provider 从自定义配置字段读自己的 WMS 地址：

```yaml
provider: "my_wms"
api_base_url: "http://localhost:2124/api"     # 留给人脸闸门
auth:
  type: api_key
  key: "wh_xxx"                                # 我们系统的 API Key
wms_base_url: "https://your-wms.example.com/api/v1"   # 你的 WMS
wms_token: "your-token"
```

```python
class MyWmsProvider(BaseProvider):
    PROVIDER_NAME = "my_wms"

    def __init__(self, config: dict):
        super().__init__(config)
        # 覆盖基类的 base_url，把 http_get/http_post 指向自己的 WMS
        self.base_url = config.get("wms_base_url", "").rstrip("/")
        self._token = config.get("wms_token", "")

    def get_auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}
```

这样不改任何核心代码，两套认证也互不干扰。

**方案 2：在你自己的后端实现 `/face/verify-mcp`**

适合完全不接我们后端的部署。不想要人脸就返回一个恒定桩：

```python
@app.post("/api/face/verify-mcp")
def verify_mcp():
    return {"status": "skipped", "failure_reason": "feature_disabled",
            "confidence": None, "matched_subject_id": None,
            "matched_subject_name": None}
```

要真做人脸就返回 `pass` 并带上 `matched_subject_name`——这个名字会作为 `actual_operator` 落到出入库记录里。请求体字段：`operation`（`stock_in`/`stock_out`/`move_batch_location`/`query`）、`warehouse_id`、可选 `image_b64` / `embedding_b64` / `embedding_model_tag`。

注意超时预算是 **18 秒**（后端可能同步直连设备抓帧再推理），你的实现别卡超过它，否则会被判 `transport_error` 而 fail-closed 误杀。

**方案 3：把 `api_base_url` 留空**

`_face_guard()` 走 `no_api_base` → `skipped`，闸门整体旁路。但 `BaseProvider` 的 `self.base_url` 也会是空，所以必须配合方案 1 的自定义字段用。

#### 你的数据库要不要建人脸相关的字段？

**人脸相关的表一个都不用你建。** 人员（`face_subjects`）、特征库（`face_enrollments`）、审计（`face_auth_logs`）、权限规则全部存在我们的 DB 里，人脸库主库也在我们这（设备上那份是通过 `push-faces` 下发的副本）。你的库零改动。

穿过边界进到你系统的只有**一个字符串** —— 人脸识别到的真实操作人姓名：

```
人脸认出「张三」 → 工具层 actual_operator="张三" → 传给你的 Provider.stock_in(...)
```

就这一个。所以你有三个选择：

| 选择 | 要做什么 | 说明 |
|---|---|---|
| **不留痕** | Provider 忽略 `actual_operator` 参数 | **零改动**。人脸照样拦，只是你的库里不记「是谁操作的」；我们这边 `face_auth_logs` 仍有完整审计，可在我们 UI 查 |
| **留痕**（推荐） | 出入库流水表加一列 | 见下 |
| **映射到已有列** | 塞进你现成的「经办人 / 操作人」字段 | 注意不要和 `operator` 混用，见下方第 3 点 |

选留痕的话，照我们 `inventory_records.actual_operator` 的定义来即可：

```sql
-- 人脸识别到的真实操作人姓名快照
ALTER TABLE <你的出入库流水表>
  ADD COLUMN actual_operator VARCHAR(255) NULL;
```

三个约束：

1. **必须可空**。非人脸场景、或规则不要求人脸的操作，该值恒为 `NULL`，不能设 NOT NULL。
2. **存姓名字符串，不是外键**。你的库里没有我们的 `face_subjects` 表，传 `subject_id` 过去没有意义，所以传的是 name 快照。
3. **和 `operator` 是两个独立字段，不要合并**。`operator` 是 LLM 填的（默认 `"MCP系统"`，可被话术伪造）；`actual_operator` 是人脸验出来的（可信）。合并等于把可信字段污染成不可信的 —— 这个区分是人脸留痕的全部价值。

#### 用我们后端时，人脸什么时候真的会拦

`/face/verify-mcp` 需要 API Key 具备 `FACE:WRITE` 权限。以下情况后端主动返回 `skipped`（放行）：

- 部署级开关关闭（`FACE_ENABLED=false`，云端版不支持人脸）→ `feature_disabled`
- 租户没开人脸或没有配置 → `feature_disabled`
- 当前 operation 的规则不要求人脸 → `rule_not_required`
- API Key 属于全局管理员、没有具体租户上下文 → `no_tenant_context`

也就是说：**接我们后端 + 不配人脸规则 = 闸门自动透明**，你不需要做任何事。只有显式配了规则才会真的拦。

### A2：完全自己写 MCP server（非仓库领域）

如果你的业务不是仓库（CRM、工单、IT 运维），Provider 的 6 个方法语义对不上，那就只借 `mcp_pipe.py` 当传输层，自己用 FastMCP 定义工具。这条路**不会引入人脸闸门**（它写在 `warehouse_mcp.py` 里，你不用这个文件就没有）；如果你想要人脸鉴权，自己在工具开头 POST 一次我们的 `/face/verify-mcp` 并按 §2.5 的语义处理返回即可。



```python
@mcp.tool()
def check_order_status(order_id: str) -> dict:
    """
    Check the status of a customer order.
    Use this when the user asks about order tracking or delivery status.

    Args:
        order_id: The unique order identifier (e.g., "ORD-2024-001")
    """
    return api_get(f"/orders/{order_id}/status")
```

```bash
export MCP_ENDPOINT="wss://<你的 MCP 端点>"
uv run python mcp_pipe.py your_server.py
```

**语音场景下的硬性约束**（踩过的坑，别省）：

- **禁止 `print()`**：stdio 是协议通道，任何 stdout 写入都会破坏 JSON-RPC 帧。一律用 `logging`（走 stderr）。
- **返回值要小**：会被 LLM 读来生成语音回复。控制在 ~1KB 内；云端单帧约 13KB，返回列表过长会撞 WebSocket 1009。`warehouse_mcp.py` 里 `max_results` 默认压到 10 就是这个原因。
- **docstring 就是工具的 UI**：LLM 靠它决定「何时调用」和「参数怎么填」。写清意图触发词，不要只写参数类型。
- **命名要可读**：`query_xiaozhi_stock` 而不是 `qry_stk`。
- **异常要吞掉并结构化返回**：`{"success": False, "error": ..., "message": ...}`，让 LLM 能把失败原因讲出来，而不是整个连接崩掉。
- **改完代码必须重启 MCP 进程**，工具列表是握手时上报的。

---

## 3. 路径 B：把别人的系统桥接到我们这里

适用于「我没有自己的仓库系统，我用的是第三方 WMS/ERP，但我想用你们这套设备 + 前端 + 语音」。

你只交付**一个 `.py` 文件**，从 Web UI 上传。我们的后端负责安全扫描、连通性测试、激活托管；MCP 工具层和前端完全不变。

### 3.1 系统模式

系统有两种模式（`system_settings.system_mode`）：

| 模式 | 数据落在哪 |
|---|---|
| `self_owned`（默认） | 本系统自带的数据库 |
| `external_erp` | 你的第三方 ERP，本系统只做 UI + 语音入口 |

切换：`PUT /api/system/mode {"mode": "external_erp"}`。**前置条件：必须已有一个激活的 Provider**，否则 400。

### 3.2 上传前：文件必须过校验

`providers/validator.py` 会做 AST 静态扫描，不通过直接 400 拒收：

| 规则 | 说明 |
|---|---|
| 文件 ≤ 100KB、扩展名 `.py` | 硬上限 |
| 禁止导入 | `os` `sys` `subprocess` `shutil` `socket` `ctypes` `code` `codeop` |
| 禁止调用 | `eval` `exec` `compile` `open` `__import__` |
| ⚠️ 禁止调用是**按函数名**匹配的 | 包括属性调用形式。也就是说 `re.compile(...)` 会被判为 `*.compile()` 违规 —— 即使 `re` 在白名单里。用 `re.match` / `re.search` 直接调，不要预编译正则 |
| 建议只用 | `requests` `json` `datetime` `logging` `hashlib` `hmac` `base64` `urllib` `time` `re` `typing` `abc` `dataclasses` |
| 必须有 | 一个 `BaseProvider` 子类，且 `PROVIDER_NAME` 为非空字符串 |
| 必须实现 6 个方法 | `resolve_name` `query_stock` `stock_in` `stock_out` `search` `get_today_statistics` |
| 可选实现 | `query_batch` `move_batch_location`（基类有默认实现，不实现时返回结构化 `not_implemented`） |

> **注意参数顺序**：连通性测试是**按位置**调用你的方法的（例如 `search("test", "material", None, None, None, False)`）。参数名可以改，顺序不能改，否则 L1 直接失败。
>
> L2 会真的往你的系统写入 `test_item` 各 1 件（`reason_category` 用 `other_in` / `other_out`），**请指向测试环境**。

因为禁用了 `os`，配置一律通过构造函数的 `config` 字典读取（对应 DB 里该 Provider 的 `config` JSON），不要试图读环境变量或文件。

### 3.3 上传 → 测试 → 激活

| 步骤 | 接口 | 说明 |
|---|---|---|
| 1. 上传 | `POST /api/erp/providers`（multipart `file`） | 过校验后落到 `providers/custom/`，DB 记录 `provider_name` / `class_name` / `filename`；同租户内 `provider_name` 重复返回 409 |
| 2. 填配置 | `PUT /api/erp/providers/{id}` | body `{name, config}`，`config` 是任意 JSON，原样传给你的 Provider 构造函数 |
| 3. Level 1 测试 | `POST /api/erp/providers/{id}/test?level=1` | **只读**：`resolve_name` / `query_stock` / `search` / `get_today_statistics`，外加三个可选探测方法 `list_tenants` / `list_warehouses` / `list_users`（**未实现不算失败**，标记 `skipped`；实现了则校验 `{success, items}`）。校验必需 key 是否存在，记录每个方法的延迟 |
| 4. Level 2 测试 | `POST /api/erp/providers/{id}/test?level=2` | **写操作**：`stock_in` / `stock_out`，会在你的 ERP 里对 `test_item` 各写 1 件。建议指向测试环境 |
| 5. 激活 | `POST /api/erp/providers/{id}/activate` | **必须 L1 全绿**，否则 400。同租户内其余 Provider 自动停用（单激活） |
| 6. 切模式 | `PUT /api/system/mode` → `external_erp` | 全系统开始走你的 ERP |
| — | `GET /api/erp/providers/{id}/status` | 实时探活，用 `get_today_statistics()` 当健康探针，返回 `{online, latency_ms, error}` |
| — | `POST /api/erp/providers/{id}/deactivate` | 停用 |

L1/L2 结果分别存在 `test_results.level1` / `.level2`，只有 L1 通过才写 `test_passed_at`。以上操作都需要 `ERP:ADMIN` 权限，且按 `tenant_id` 隔离——跨租户操作 403。

### 3.4 MCP 侧如何拿到你的 Provider

`warehouse_mcp.py` 启动时不读数据库，而是调 `GET /api/erp/providers/active-for-mcp`，由后端按 API Key 推导出的 `tenant_id` 做范围过滤后返回激活 Provider（这是为了消除早期直接裸查 sqlite 造成的跨租户泄露）。

行为约定：

- 模式为 `self_owned` → 用 `DefaultProvider`；
- `external_erp` 且有激活 Provider → 动态从 `providers/custom/<filename>` 加载，配置为 `{**config.yml, **DB里的config}`；
- **任何异常都回退到 `DefaultProvider`**（网络失败、404、文件缺失、加载抛错），并打 warning。所以「改了 ERP 却发现数据还写进本地库」时，第一件事是看 MCP 日志里的 fallback warning。

> **多租户路径（2026-08 已修复）**：上传时文件按租户隔离存到
> `providers/custom/<tenant_id>/<filename>`，而早期 MCP 加载时只找扁平的
> `providers/custom/<filename>`，导致多租户下「上传 + 激活」后静默回退到默认
> Provider。现在 `active-for-mcp` 会返回 `tenant_id`，加载器按
> 「租户子目录 → 扁平路径」顺序解析，两种布局都兼容，找不到时会把尝试过的
> 候选路径打进日志。**注意镜像版本**：旧镜像没有这个修复，那种环境下需要把
> 文件放在扁平路径。

### 3.5 外部作用域绑定（多仓库 / 多组织时需要）

接了外部 ERP 之后，**我方的租户/仓库与对方的没有任何对应关系**。与其在本地镜像
一套对方的组织结构（双重维护、必然漂移），不如让 Provider 把「对方有什么」报上来，
用户在配置智能体时直接选，我们只存**选中的原始编码**并在调用时原样透传。

三个可选探测方法见
[WMS_Provider_Development.md §外部作用域探测](../docs/WMS_Provider_Development.md)。
按对方系统形态实现即可，**一个都不实现也能用**（界面退化为手工填写编码）：

| 对方系统形态 | 需要实现 | 智能体配置界面 |
|---|---|---|
| 单组织、单仓库 | 都不用 | 两个字段留空，调用时用 Provider 配置里的固定值 |
| 单组织、多仓库 | `list_warehouses` | 租户手填（留空），仓库是下拉 |
| 多组织、多仓库 | 两个都实现 | 两级联动下拉 |

只返回一个候选时界面自动选中，用户无需操作。

选定后的值存在 `mcp_connections.external_tenant_id` / `external_warehouse_id`，
运行时注入 Provider 的 `config`：

```python
def __init__(self, config: dict):
    super().__init__(config)
    self.tenant_id = config.get("external_tenant_id") or config.get("tenant_id")
    self.warehouse_id = config.get("external_warehouse_id") or config.get("warehouse_id", "default")
```

**每个智能体一个 Provider 实例**，所以多个智能体绑不同仓库时天然隔离，
不需要在方法里自己区分调用来源。

相关接口：`GET /api/erp/external/tenants`、`GET /api/erp/external/warehouses`
（恒返回 200，`not_implemented` 是预期路径，不表现为 HTTP 错误）。

### 3.6 身份导入：授权始终由我方判定

**这一点不能推给对方**：谁能登录我方系统、谁能配哪个智能体、谁能改人脸规则，
走的是我方 `users(role, tenant_id)` + `user_warehouses` 这条链。即便库存数据全在
对方，这份「用户 → 租户/角色」的归属数据仍必须落在我方，否则整个权限体系是空的。

导入只是免去管理员照着对方的用户表手工重敲一遍。**两条来源，任选其一**：

| 来源 | 对方要做什么 | 说明 |
|---|---|---|
| Provider 探测 | 实现 `list_users()` | 界面上点「从外部系统探测」 |
| 自己组织 JSON | **什么都不用做** | 粘贴或上传文件；导入接口纯落库，不依赖 Provider |

JSON 格式（三种包法都认：裸数组、`{items:[...]}`、`{users:[...]}`）：

```json
[
  {"id": "u1001", "name": "zhangsan", "display_name": "张三"},
  {"id": "u1002", "name": "lisi"}
]
```

`id` 与 `name` 必需，`display_name` 可选。**`id` 必须稳定**——它是去重键，对方那边
若会变，重复导入就会建出重复用户而不是更新。

对方**不需要**提供的东西：

| | 为什么 |
|---|---|
| 密码 | 我方本地管理，对方无需暴露任何认证接口 |
| 角色 | 对方不了解我方权限模型，由我方管理员导入时逐行指定 |
| 租户归属 | 同上，导入时决定归到我方哪个租户 |

界面位置：**系统设置 → 数据管理 →「从外部系统导入身份」**（仅 `external_erp` 模式显示）。
接口：`POST /api/erp/external/import/users`，需 `USERS:ADMIN`。

幂等行为：

- 同 `external_user_id` 再导 → **更新** username/display_name/role，**不动密码**
- 我方已有同名但非同一外部账号 → **跳过并回报**，不覆盖（主要保护本地管理员账号）
- 之前手工建的本地用户 `external_user_id` 为空，不受影响

> **导入的用户只承载权限**，与出入库的 `operator`、人脸库都没有关联。
> `operator` 是自由填写的文本，人脸是单独录入的。不要在三者之间建隐式关联。

### 3.7 人脸识别在外部模式下的作用域

人脸规则是**仓库级优先、租户级兜底**，作用域键是**我方的** `warehouse_id`：

- 对方**有**租户概念 → 权限与规则做到租户级即可，本地一个仓库都不用建
- 对方**没有**租户概念 → 仓库是唯一的作用域维度，此时把对方仓库导入为本地行
  **仅作权限锚点**（`user_warehouses` 必须绑本地 `warehouse_id`），不承载任何库存

接口：`POST /api/erp/external/import/warehouses`，导入的行会带上
`warehouses.external_warehouse_id`。

⚠️ **智能体绑定必须与锚点对齐**：人脸规则挂在本地 `warehouse_id` 上，而调用透传的是
`external_warehouse_id`。两者若各选各的，会出现「规则配在北京仓、智能体其实绑了
上海仓」——**规则静默不生效且没有任何报错**。配置界面已做自动联动（选定外部仓库后
自动把本地仓库切到对应锚点），但通过 API 直接建连接时要自己保证一致。

### 3.8 外部模式下本地页面是空的

看板、进出库记录、库存列表、产品详情这四个页面读的是**我方本地库**，而外部模式下
业务数据全在对方系统 —— 这些页面会是空的。界面顶部有提示横幅说明这一点。

**这不是故障。** 真实数据请到对方系统查看。

### 3.9 端到端最小示例

完整的 `AcmeWmsProvider` 示例（6 个方法全部实现 + 对应 `config.yml`）见 [WMS_Provider_Development.md](../docs/WMS_Provider_Development.md#完整示例)。

---

## 4. 调试

**不启动整条 MCP 链路，单独测 Provider：**

```bash
cd mcp
uv run python -c "
import yaml
from providers import load_provider
config = yaml.safe_load(open('config.yml'))
p = load_provider(config)
print(p.resolve_name('螺丝'))
print(p.query_stock('M3螺丝'))
print(p.get_today_statistics())
"
```

**直接跑上传前的校验和测试：**

```bash
uv run python -c "
from providers.validator import validate_provider_file
print(validate_provider_file('providers/custom/my_wms.py'))
"
uv run python -c "
from providers.test_runner import run_level1_tests
print(run_level1_tests('providers/custom/my_wms.py', {'api_base_url': 'https://...'}))
"
```

**其他：**

```bash
export LOG_LEVEL=DEBUG          # 详细日志
uv run python warehouse_mcp.py  # 裸跑 stdio server
npx @modelcontextprotocol/inspector uv run python warehouse_mcp.py   # 图形化调工具
```

`mcp_pipe.py` 的协议事件日志默认开启（`MCP_PROTOCOL_EVENT_LOG=0` 关闭），日志目录由 `MCP_PIPE_LOG_DIR` 控制（容器内默认 `/app/logs`）。

## 5. 常见问题

| 症状 | 原因 | 处理 |
|---|---|---|
| `未知的 provider 'xxx'` | `PROVIDER_NAME` 与 `config.yml` 的 `provider` 不一致 | 对齐拼写；确认 `.py` 在 `providers/` 或 `providers/custom/` 下 |
| 上传 400 「校验失败」 | 命中禁用导入/内置，或缺方法 | 按 §3.2 表逐条核对，`errors` 数组会列出全部问题 |
| L1 测试全红且 error 是 `Provider 加载失败` | 构造函数抛异常（常见：读了不存在的 config 字段） | 用 `config.get(k, default)`，别用 `config[k]` |
| L1 某方法 `缺少必需字段` | 返回 dict 缺 key | 对照 §3.3 的必需 key 补齐 |
| 查询/入库正常，**只有出库 TypeError** | `stock_out` 漏了 `allow_partial_fallback` 参数 | 补上该参数（工具层无条件按关键字传它） |
| 激活报 400「请先通过 Level 1 测试」 | 没跑或没全绿 | 先 `POST .../test?level=1` |
| 切 `external_erp` 报 400 | 没有激活的 Provider | 先激活 |
| 切了 `external_erp` 但数据仍写本地库 | MCP 侧 fallback 了 | 看 MCP 日志的 warning，里面会列出尝试过的 Provider 文件路径；核对 §3.4 |
| MCP 连接卡在 `Connecting to WebSocket server...` | 企业防火墙封 WSS / 端点写错 | 手机热点验证；确认 `wss://` 前缀；必要时设 `HTTPS_PROXY` |
| 语音说了但工具没触发 | 工具名/docstring 不够清楚，或没重启 | 改 docstring 描述意图，重启 MCP 进程 |
| 返回内容长时连接被断（1009） | 单帧超限 | 收紧 `max_results`，精简返回字段 |
| 所有工具（含查询）都返回 `face_auth_denied:http_404` | `api_base_url` 指向的后端没有 `/face/verify-mcp` | 按 §2.5 三种解法之一处理（最常见是方案 1：拆分 URL） |
| 全部工具返回 `face_auth_denied:transport_error` | 闸门地址不可达，或你的 `/face/verify-mcp` 超过 18s | 检查 `api_base_url` 连通性；缩短实现耗时 |
| 出入库记录里 `actual_operator` 为空 | 人脸未启用或规则未要求 | 预期行为；需要留痕请配人脸规则 |
| 401 Unauthorized | `auth` 块配置或 API Key 失效 | Web UI「用户管理 → API 密钥」重建 |
| `/face/verify-mcp` 返回 403 | API Key 缺 `FACE:WRITE` 权限 | 用有该权限的 Key |
| 外部模式下库存/记录/看板页面是空的 | 数据在对方系统，这些页面读的是本地库 | **不是故障**，见 §3.8。真实数据到对方系统查看 |
| 配了人脸规则但出入库根本不拦 | 智能体绑的仓库与规则的仓库对不上，规则静默不生效 | 见 §3.7 的警告。界面已自动联动；用 API 直接建连接的要自己对齐 |
| 智能体配置里外部租户/仓库是输入框不是下拉 | Provider 没实现对应的探测方法 | 预期行为，手工填编码即可；要下拉就实现 §3.5 的方法 |
| 探测报「当前租户没有激活的 ERP Provider」 | 还没激活 Provider | 先按 §3.3 上传并激活；或改用粘贴 JSON 导入（不依赖 Provider） |
| 导入用户后少了几条 | 同租户下已存在同名但非同一外部账号的用户，被跳过保护 | 看返回的 `skipped` 数组，每条都带原因；改名或手工处理 |
| 导入的用户登录不了 | 用的不是导入时设的初始密码 | 用导入时填的初始密码登录，登录后自行修改 |
| 重复导入建出了重复用户 | 对方的账号 `id` 不稳定（去重键变了） | 让对方用稳定不变的账号 ID，见 §3.6 |

## 相关文档

- [MCP_README.md](MCP_README.md) — 工具级返回字段参考
- [../docs/WMS_Provider_Development.md](../docs/WMS_Provider_Development.md) — Provider 接口契约与完整示例（中英双语）
- [../docs/MCP_External_System_Integration.md](../docs/MCP_External_System_Integration.md) — 设备端从零接入（含 SenseCraft 端点获取）
- [../docs/MCP_Server_Development_Practice.md](../docs/MCP_Server_Development_Practice.md) — MCP server 开发实践
- [MCP_SLIM_DESIGN.md](MCP_SLIM_DESIGN.md) — 工具集精简与 token 预算设计
- [../docs/CLAUDE_DESKTOP_CONFIG.md](../docs/CLAUDE_DESKTOP_CONFIG.md) — Claude Desktop 配置
