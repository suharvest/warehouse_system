# WMS Provider 开发指南

[English](#english) | 中文

本文档说明如何编写自定义 Provider，让 MCP 语音控制对接你自己的 WMS（仓库管理系统）后端，**无需修改任何 MCP 工具代码**。

> 📖 **先读 [mcp/README.md](../mcp/README.md)** —— 那里说明了两条集成路径（自己部署 MCP / 把 Provider 上传给我们托管）、人脸闸门、以及上传校验流程。本文只讲 **Provider 接口契约**本身。
>
> ⚠️ **签名以 `mcp/providers/base.py` 为准。** 本文档已于 2026-08 对齐到当前代码。如果你看到的示例和 `base.py` 不一致，以 `base.py` 为准并提 issue。

## 架构概览

```
Watcher 语音 → MCP Endpoint → warehouse_mcp.py → Provider → 你的 WMS API
                                    │
                                    ├── DefaultProvider  (自有后端)
                                    ├── YourWmsProvider   (你的 WMS)
                                    └── ...               (更多)
```

MCP 工具层（`warehouse_mcp.py`）通过 Provider 接口与后端通信。切换 WMS 只需：

1. 在 `mcp/providers/` 目录新建一个 `.py` 文件（上传托管的话放 `providers/custom/`）
2. 继承 `BaseProvider`，实现 6 个必需方法（另有 2 个可选）
3. 在 `config.yml` 中指定 `provider` 名称

系统会自动扫描 `providers/` 及 `providers/custom/` 目录，发现并注册所有 Provider，无需手动注册。

## 快速开始

### 1. 创建 Provider 文件

在 `mcp/providers/` 目录下新建文件，例如 `my_wms.py`：

```python
"""对接 MyWMS 系统的 Provider"""

from .base import BaseProvider


class MyWmsProvider(BaseProvider):
    """MyWMS 后端适配器。"""

    # 此名称对应 config.yml 的 provider 字段
    PROVIDER_NAME = "my_wms"

    # ── 6 个必需方法 ──

    def resolve_name(self, text, entity_type="all"):
        ...

    def query_stock(self, product_name, show_batches=False):
        ...

    def stock_in(self, product_name, quantity, reason_category, reason_note,
                 operator, fuzzy, location=None, contact_id=None,
                 variant=None, allow_new_variant=False, actual_operator=None):
        ...

    def stock_out(self, product_name, quantity, reason_category, reason_note,
                  operator, fuzzy, variant=None, location=None, batch_no=None,
                  location_fuzzy=False, allow_partial_fallback=False,
                  actual_operator=None):
        ...

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        ...

    def get_today_statistics(self):
        ...

    # ── 2 个可选方法（不实现则对应工具返回 not_implemented）──

    def query_batch(self, batch_no):
        ...

    def move_batch_location(self, batch_no, new_location, quantity=None,
                            from_location=None, product_name=None,
                            operator="MCP系统"):
        ...
```

> ⚠️ **参数顺序不能改。** 工具层和连通性测试都**按位置**传参。参数名可以自己取，顺序错了就是 TypeError。尤其注意 `stock_out` 的 `allow_partial_fallback` —— 工具层无条件按关键字传入，漏声明会导致**每次出库都失败**。

### 2. 修改配置

编辑 `mcp/config.yml`：

```yaml
provider: "my_wms"
api_base_url: "https://your-wms.example.com/api"
auth:
  type: bearer
  token: "your-access-token"
timeout: 15
```

> 如果你要同时用我们的人脸识别，`api_base_url` 必须留给我们的后端，你的 WMS 地址另用自定义字段。原因和写法见 [mcp/README.md §2.5](../mcp/README.md)。

### 3. 启动

```bash
cd mcp
./start_mcp.sh
```

日志中会显示 `使用 provider: my_wms (MyWmsProvider)`，确认切换成功。

## BaseProvider 接口详解

### 构造函数

```python
def __init__(self, config: dict):
```

`config` 是 `config.yml` 的完整内容（上传托管时是该 Provider 在数据库里的 `config` JSON 与 `config.yml` 的合并结果）。你可以在其中添加自定义字段：

```yaml
provider: "my_wms"
api_base_url: "https://your-wms.example.com/api"
auth:
  type: bearer
  token: "xxx"
# 自定义字段
warehouse_id: "WH-001"
company_code: "ACME"
```

在 Provider 中读取：

```python
def __init__(self, config: dict):
    super().__init__(config)
    self.warehouse_id = config.get("warehouse_id", "")
    self.company_code = config.get("company_code", "")
```

> 一律用 `config.get(k, default)`，不要用 `config[k]`。构造函数抛异常会让连通性测试整体判为「Provider 加载失败」，四个方法一起变红，很难定位。

### 内置 HTTP 工具

基类提供了 `http_get` 和 `http_post` 方法，自动处理认证头和错误：

```python
# GET 请求
data = self.http_get("/inventory/items", params={"sku": "ABC123"})

# POST 请求
result = self.http_post("/inventory/inbound", data={"sku": "ABC123", "qty": 10})
```

出错时返回 `{"error": "..."}` 而不是抛异常，所以每个方法都要检查 `"error" in data`。

如果你的 WMS API 格式与默认不同，可以 override 这两个方法或 `get_auth_headers()`。基类把 `config["api_base_url"]` 读进 `self.base_url`；要让 HTTP 打到别的地址，在 `__init__` 里覆盖 `self.base_url` 即可。

### 认证方式

在 `config.yml` 的 `auth` 块中配置，基类自动处理：

| type | 配置字段 | 生成的 Header |
|------|----------|--------------|
| `api_key` | `key`, `header`(可选，默认 `X-API-Key`) | `X-API-Key: <key>` |
| `bearer` | `token` | `Authorization: Bearer <token>` |
| `basic` | `username`, `password` | `Authorization: Basic <base64>` |
| `custom` | — | 由子类 override `get_auth_headers()` |

自定义签名示例（如 HMAC）：

```python
import hashlib
import hmac
import time

class MyWmsProvider(BaseProvider):
    PROVIDER_NAME = "my_wms"

    def get_auth_headers(self) -> dict:
        secret = self.auth_config.get("secret", "")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            secret.encode(), timestamp.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }
```

## 6 个必需方法

### 1. `resolve_name(text, entity_type) -> dict`

将模糊文本解析为系统中的精确实体名称。

**参数：**
- `text`: 用户输入的模糊文本（如语音识别结果 "螺丝钉"）
- `entity_type`: `"material"` | `"contact"` | `"operator"` | `"all"`

**返回格式：**

```python
{
    "best_match": {"name": "M3螺丝", "score": 92.5, "entity_type": "material", "id": 1},
    "confident": True,         # True 表示可直接使用 best_match
    "candidates": [            # confident=False 时提供候选列表
        {"name": "M3螺丝", "score": 92.5, ...},
        {"name": "M4螺丝", "score": 78.0, ...},
    ]
}
```

**必需字段（连通性测试会校验）：** `best_match`、`confident`

**实现建议：** 如果外部 WMS 有搜索 API，直接调用即可。如果没有，可以拉取物料列表后用 `rapidfuzz` 本地匹配。

### 2. `query_stock(product_name, show_batches=False) -> dict`

查询产品库存。

**返回格式：**

```python
# 成功
{
    "success": True,
    "product": {
        "name": "M3螺丝",
        "sku": "SKU-001",
        "current_stock": 500,
        "unit": "个",
        "safe_stock": 100,
        "location": "A区-01架",
        "status": "正常",            # "正常" | "偏低" | "告急"
        "variant": "M3x10",          # 可选：同名多规格时的规格
    },
    "batches": [...],                 # show_batches=True 时提供
    "message": "查询成功：M3螺丝 当前库存 500 个",
}

# 失败（有候选）
{
    "success": False,
    "error": "名称 '螺丝' 不够明确",
    "candidates": [{"name": "M3螺丝", ...}, {"name": "M4螺丝", ...}],
    "message": "找到多个候选：M3螺丝, M4螺丝，请指定更精确的名称",
}
```

**必需字段：** `success`

### 3. `stock_in(...) -> dict`

产品入库。

```python
def stock_in(self, product_name, quantity, reason_category, reason_note,
             operator, fuzzy, location=None, contact_id=None,
             variant=None, allow_new_variant=False, actual_operator=None):
```

| 参数 | 说明 |
|---|---|
| `reason_category` | 入库原因枚举：`purchase` \| `return` \| `refund` \| `produce` \| `transfer_in` \| `other_in`（也接受中文别名，建议自己做一次归一化并对未知值 fail-closed） |
| `reason_note` | 自由文本备注，可能为空字符串或 `None` |
| `operator` | LLM/设备填的操作人，默认 `"MCP系统"`，**不可信**（可被话术伪造） |
| `fuzzy` | 是否允许模糊匹配物料名，工具层恒传 `True` |
| `variant` | 同名多规格时的规格值 |
| `allow_new_variant` | 允许创建新规格；默认 `False`，需用户确认后才为 `True` |
| `actual_operator` | **人脸识别到的真实操作人姓名快照**，可信；未启用人脸时为 `None` |

**返回格式：**

```python
{
    "success": True,
    "message": "入库成功：M3螺丝 入库 100 个",
    "product": {
        "name": "M3螺丝",
        "unit": "个",
        "in_quantity": 100,    # 本次入库量
        "new_quantity": 600,   # 入库后库存
    },
    "batch": {},               # 不做批次管理就给空 dict
}
```

**必需字段：** `success`

> ⚠️ **字段名必须完全一致。** 工具层的语音播报直接读
> `product.name` / `product.unit` / `product.in_quantity` / `product.new_quantity`
> （见 `mcp/warehouse_mcp.py` 的 `stock_in` 分支）。用 `product_name` / `quantity` /
> `new_stock` 这类平铺字段**不会报错**，只会让小智播成
> 「已入库M3螺丝**?**个，当前库存**?**个」——排查起来非常费时。

> `operator` 与 `actual_operator` 是两个独立字段，**不要合并**。要不要在自己库里存 `actual_operator`、怎么建那一列，见 [mcp/README.md §2.5](../mcp/README.md)。

### 4. `stock_out(...) -> dict`

产品出库。

```python
def stock_out(self, product_name, quantity, reason_category, reason_note,
              operator, fuzzy, variant=None, location=None, batch_no=None,
              location_fuzzy=False, allow_partial_fallback=False,
              actual_operator=None):
```

除与 `stock_in` 同名的参数外：

| 参数 | 说明 |
|---|---|
| `reason_category` | 出库原因枚举：`sell` \| `lend` \| `consume` \| `loss` \| `transfer_out` \| `other_out`（`use`→`consume`、`scrap`→`loss` 等别名建议一并处理） |
| `batch_no` | 非空时**只**从该批次扣减，不足即报错，不要自动 fallback 到其他批次 |
| `location_fuzzy` | 对 `location` 做作用域模糊匹配（仅 MCP 调用时为 `True`） |
| `allow_partial_fallback` | 指定批次/库位不足时，是否允许从其余库存补足。**默认 `False`** —— 工具层会先返回 `awaiting_confirm` 让用户确认，同意后才带 `True` 重发。**必须声明这个参数**，否则每次出库都 TypeError |

返回格式同 `stock_in`，但出库量的字段名是 **`out_quantity`**（不是 `in_quantity`）：

```python
{
    "success": True,
    "message": "出库成功：M3螺丝 出库 20 个",
    "product": {
        "name": "M3螺丝",
        "unit": "个",
        "out_quantity": 20,
        "new_quantity": 580,
    },
    "batch_consumptions": [],  # 不做批次管理就给空 list
}
```

**必需字段：** `success`

失败时若是「名称对不上唯一物料」，`error` 必须是 **`ambiguous_name`** 并带 `candidates`，
工具层才会转成「我不确定你说的是哪一个，候选有……」的追问；用别的错误码（如 `ambiguous`）
会直接播成一句失败，用户无从选择。

### 5. `search(...) -> dict`

统一搜索。

```python
def search(self, query, entity_type, category, status, contact_type, fuzzy,
           include_batches=False, max_results=0):
```

`max_results=0` 表示用配置里的默认上限。

**返回格式：**

```python
{
    "success": True,
    "count": 3,              # 本次返回数量
    "total": 15,             # 总匹配数
    "items": [
        {"name": "M3螺丝", "sku": "SKU-001", "current_stock": 500, ...},
        ...
    ],
    "message": "搜索物料成功，找到 15 条匹配记录",
}
```

**必需字段：** `success`、`items`

> **注意响应体积。** 云端单帧约 13 KB，返回过长会触发 WebSocket close 1009 直接断连。`DefaultProvider` 的做法是按相关度从尾部裁剪直到序列化后小于预算，建议照做。

### 6. `get_today_statistics() -> dict`

当天统计。

**返回格式：**

```python
{
    "success": True,
    "date": "2026-03-24",
    "statistics": {
        "today_in": 120,
        "today_out": 80,
        "total_stock": 5000,
        "low_stock_count": 3,
        "net_change": 40,
    },
    "message": "查询成功：2026-03-24 入库 120 件，出库 80 件",
}
```

**必需字段：** `success`、`statistics`

> 这个方法同时被用作**健康探针**（`GET /api/erp/providers/{id}/status`），请保证它足够轻量。

## 2 个可选方法

这两个在 `BaseProvider` 里有默认实现（返回结构化的 `not_implemented`），不实现也能实例化，但对应的 MCP 工具会一直失败。**新 Provider 建议都实现。**

### `query_batch(batch_no) -> dict`

按批次号查询批次详情（只读）。

```python
{
    "success": True,
    "batch": {"batch_no": "B003", "product_name": "M3螺丝",
              "quantity": 12, "location": "A区-01架", ...},
    "message": "批次 B003：M3螺丝，余量 12 个，位于 A区-01架",
}
```

查不到时返回 `{"success": False, "error": "batch_not_found", "message": ...}`。

### `move_batch_location(...) -> dict`

批次库位移动，支持部分数量拆分。

```python
def move_batch_location(self, batch_no, new_location, quantity=None,
                        from_location=None, product_name=None,
                        operator="MCP系统"):
```

- `quantity` 为 `None` 或等于批次余量 → 整批移位
- `quantity` 小于余量 → 拆分：源批次扣减，目标库位创建同物料新批次

```python
{
    "success": True,
    "operation": "move_batch_location",
    "moved_quantity": 5,
    "source_batch": {...},
    "target_batch": {...},
    "message": "已将批次 B003 的 5 个移至 B区-02架",
}
```

## 外部作用域探测（可选，多仓库/多组织时需要）

绑定外部 ERP 后，我方的租户/仓库跟贵方的**没有任何对应关系**。我们不会在本地
镜像一套贵方的组织结构（那必然带来双重维护和数据漂移），而是反过来：由 Provider
把"贵方有什么"报上来，用户在配置智能体时直接选，我们只存选中的**原始编码**并在
调用时原样透传。

这两个方法同样是可选的（基类默认返回 `not_implemented`），按贵方系统的形态实现即可：

| 贵方系统形态 | 需要实现 | 配置界面的表现 |
|---|---|---|
| 单组织、单仓库 | 都不用实现 | 两个字段留空即可，调用时用 Provider 自身配置里的固定值 |
| 单组织、多仓库 | 只实现 `list_warehouses` | 租户退化为手工输入（留空），仓库是下拉 |
| 多组织、多仓库 | 两个都实现 | 两级联动下拉：先选组织，再选该组织下的仓库 |

只返回一个候选时，界面会自动选中，用户无需操作。

### `list_tenants() -> dict`

```python
def list_tenants(self):
    data = self.http_get("/api/orgs")
    return {
        "success": True,
        "items": [{"id": o["code"], "name": o["title"]} for o in data["list"]],
        "message": "ok",
    }
```

`items[].id` 是会被原样存下来、并在后续调用中回传给你的编码；`name` 只用于界面显示。

### `list_warehouses(tenant_id=None) -> dict`

```python
def list_warehouses(self, tenant_id=None):
    params = {"org": tenant_id} if tenant_id else None
    data = self.http_get("/api/warehouses", params=params)
    return {
        "success": True,
        "items": [{"id": w["code"], "name": w["name"]} for w in data["list"]],
        "message": "ok",
    }
```

`tenant_id` 是用户选中的组织编码；贵方系统若没有组织概念，忽略该参数即可。

失败时返回 `{"success": False, "error": "...", "items": [], "message": "..."}`，
界面会退化成手工填写，不会阻断配置。

### `list_users(tenant_id=None) -> dict`（可选）

用途跟租户/仓库探测不同：**授权是我方的责任，推不出去。** 谁能登录我方系统、
谁能配哪个智能体、谁能改人脸规则，走的是我方 `users(role, tenant_id)` +
`user_warehouses` 这条链。所以即便库存数据全在贵方，「用户 → 租户/角色」这份
归属数据仍必须落在我方——本方法只是免去管理员照着贵方的用户表手工重敲一遍。

```python
def list_users(self, tenant_id=None):
    params = {"org": tenant_id} if tenant_id else None
    data = self.http_get("/api/users", params=params)
    return {
        "success": True,
        "items": [
            {
                "id": u["id"],                 # 必需：稳定不变的账号 ID（去重键）
                "name": u["login"],            # 必需：登录名
                "display_name": u["realName"], # 可选
                # 强烈建议：该账号能访问的仓库编码。我方对非管理员只认显式仓库授权，
                # 不给的话导入的用户登录后仓库列表是空的、几乎什么都做不了。
                "warehouses": u.get("warehouseCodes") or [],
            }
            for u in data["list"]
        ],
        "message": "ok",
    }
```

导入后：`items[].id` 存进我方 `users.external_user_id`（用于增量同步与去重），
`name` 作为登录名，`display_name` 作为显示名，`warehouses` 转成我方的仓库授权。
**密码由我方本地管理**，贵方无需提供任何认证接口。

`warehouses` 里的编码需要先通过仓库导入建成本地锚点；未匹配到的会在导入结果的
`unmatched_warehouses` 里回报，不会静默丢弃。`admin` 角色不做逐仓授权——它天然可见
本租户全部仓库。

导入是幂等的：同 `external_user_id` 再导一次是更新而非重复创建；若我方已存在
同名但非同一外部账号的用户，会跳过并回报，不会静默覆盖（尤其保护本地管理员账号）。

> **导入进来的用户只承载权限，不参与业务链路。** 它既不是出入库的 `operator`
> （那是自由填写的文本），也不对应人脸库里的人（人脸是单独录入的）。用户的作用
> 只是决定谁有权修改这些配置。不要在三者之间建隐式关联。

### 调用时怎么拿到用户选的值

用户选定后，这两个编码会注入 Provider 的 `config`，在 `__init__` 里读即可：

```python
def __init__(self, config: dict):
    super().__init__(config)
    # 用户在"智能体配置"里选的贵方组织/仓库；未配置时回退到自己的默认值
    self.tenant_id = config.get("external_tenant_id") or config.get("tenant_id")
    self.warehouse_id = config.get("external_warehouse_id") or config.get("warehouse_id", "default")
```

**每个智能体一个 Provider 实例**，所以多个智能体绑不同仓库时天然隔离，
不需要你在方法里自己区分。

## 完整示例

以下是一个对接假想 "AcmeWMS" 系统的完整 Provider 示例：

```python
"""对接 AcmeWMS 的 Provider

AcmeWMS REST API 文档：https://docs.acme-wms.example.com
"""

import logging
from datetime import datetime

from .base import BaseProvider

logger = logging.getLogger("WarehouseMCP")

# 出入库原因枚举 → AcmeWMS 自己的单据类型
_IN_REASON = {
    "purchase": "PO", "return": "RTN", "refund": "RFD",
    "produce": "MO", "transfer_in": "TRI", "other_in": "OTH",
}
_OUT_REASON = {
    "sell": "SO", "lend": "LND", "consume": "CSM",
    "loss": "LOS", "transfer_out": "TRO", "other_out": "OTH",
}


class AcmeWmsProvider(BaseProvider):
    """AcmeWMS 后端适配器。"""

    PROVIDER_NAME = "acme_wms"

    def __init__(self, config: dict):
        super().__init__(config)
        self.warehouse_id = config.get("warehouse_id", "default")

    # ── 1. 模糊名称解析 ──

    def resolve_name(self, text, entity_type="all"):
        result = self.http_get("/search/fuzzy", params={
            "q": text,
            "type": entity_type,
            "warehouse": self.warehouse_id,
        })
        if not result or "error" in result:
            return {"best_match": None, "confident": False, "candidates": []}

        hits = result.get("hits", [])
        if not hits:
            return {"best_match": None, "confident": False, "candidates": []}

        candidates = [
            {"name": h["name"], "score": h["relevance"], "entity_type": h["type"], "id": h["id"]}
            for h in hits[:10]
        ]
        best = candidates[0]
        gap = best["score"] - candidates[1]["score"] if len(candidates) > 1 else 100
        confident = best["score"] >= 85 and gap >= 10

        return {"best_match": best, "confident": confident, "candidates": candidates}

    # ── 2. 库存查询 ──

    def query_stock(self, product_name, show_batches=False):
        data = self.http_get("/inventory/query", params={
            "name": product_name,
            "warehouse": self.warehouse_id,
        })
        if "error" in data:
            return {"success": False, "error": data["error"], "message": f"查询失败: {data['error']}"}

        item = data["item"]
        stock = item["quantity"]
        safe = item.get("safety_stock", 0)
        status = "正常" if stock >= safe else ("偏低" if stock >= safe * 0.5 else "告急")

        result = {
            "success": True,
            "product": {
                "name": item["name"],
                "sku": item.get("sku", ""),
                "current_stock": stock,
                "unit": item.get("unit", "个"),
                "safe_stock": safe,
                "location": item.get("location", ""),
                "status": status,
            },
            "message": f"查询成功：{item['name']} 当前库存 {stock} {item.get('unit', '个')}，状态：{status}",
        }

        if show_batches:
            batches = self.http_get("/inventory/batches", params={
                "item_id": item["id"],
                "warehouse": self.warehouse_id,
            })
            result["batches"] = batches.get("items", [])

        return result

    # ── 3. 入库 ──

    def stock_in(self, product_name, quantity, reason_category, reason_note,
                 operator, fuzzy, location=None, contact_id=None,
                 variant=None, allow_new_variant=False, actual_operator=None):
        payload = {
            "item_name": product_name,
            "quantity": quantity,
            "doc_type": _IN_REASON.get(reason_category, "OTH"),
            "remark": reason_note or "",
            "operator": operator,
            "warehouse": self.warehouse_id,
        }
        if location:
            payload["location"] = location
        if contact_id is not None:
            payload["supplier_id"] = contact_id
        if variant:
            payload["spec"] = variant
        if allow_new_variant:
            payload["create_spec_if_missing"] = True
        # 人脸识别到的真实操作人（可信），与 operator 分开记账
        if actual_operator:
            payload["verified_operator"] = actual_operator

        result = self.http_post("/inventory/inbound", data=payload)
        if "error" in result:
            return {"success": False, "error": result["error"], "message": f"入库失败: {result['error']}"}

        return {
            "success": True,
            "message": f"入库成功：{product_name} 入库 {quantity} 件",
            "product": {
                "name": product_name,
                "unit": result.get("unit", "个"),
                "in_quantity": quantity,
                "new_quantity": result.get("new_quantity", 0),
            },
            "batch": {},
        }

    # ── 4. 出库 ──

    def stock_out(self, product_name, quantity, reason_category, reason_note,
                  operator, fuzzy, variant=None, location=None, batch_no=None,
                  location_fuzzy=False, allow_partial_fallback=False,
                  actual_operator=None):
        payload = {
            "item_name": product_name,
            "quantity": quantity,
            "doc_type": _OUT_REASON.get(reason_category, "OTH"),
            "remark": reason_note or "",
            "operator": operator,
            "warehouse": self.warehouse_id,
        }
        if variant:
            payload["spec"] = variant
        if location:
            payload["location"] = location
        if batch_no:
            # 指定批次时严格扣该批次，不足直接失败
            payload["batch_no"] = batch_no
            payload["strict_batch"] = not allow_partial_fallback
        if actual_operator:
            payload["verified_operator"] = actual_operator

        result = self.http_post("/inventory/outbound", data=payload)
        if "error" in result:
            return {"success": False, "error": result["error"], "message": f"出库失败: {result['error']}"}

        return {
            "success": True,
            "message": f"出库成功：{product_name} 出库 {quantity} 件",
            "product": {
                "name": product_name,
                "unit": result.get("unit", "个"),
                "out_quantity": quantity,
                "new_quantity": result.get("new_quantity", 0),
            },
            "batch_consumptions": [],
        }

    # ── 5. 搜索 ──

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        params = {"type": entity_type, "limit": max_results or 10}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if status:
            params["status"] = status

        data = self.http_get("/search", params=params)
        if "error" in data:
            return {"success": False, "error": data["error"], "message": f"搜索失败: {data['error']}"}

        items = data.get("items", [])
        total = data.get("total", len(items))
        type_label = {"material": "物料", "contact": "联系方", "operator": "操作员"}.get(entity_type, entity_type)

        return {
            "success": True,
            "count": len(items),
            "total": total,
            "items": items,
            "message": f"搜索{type_label}成功，找到 {total} 条匹配记录",
        }

    # ── 6. 当天统计 ──

    def get_today_statistics(self):
        today = datetime.now().strftime("%Y-%m-%d")
        data = self.http_get("/statistics/daily", params={
            "date": today,
            "warehouse": self.warehouse_id,
        })
        if "error" in data:
            return {"success": False, "error": data["error"], "message": f"查询统计失败: {data['error']}"}

        return {
            "success": True,
            "date": today,
            "statistics": {
                "today_in": data.get("inbound", 0),
                "today_out": data.get("outbound", 0),
                "total_stock": data.get("total_stock", 0),
                "low_stock_count": data.get("low_stock_count", 0),
                "net_change": data.get("inbound", 0) - data.get("outbound", 0),
            },
            "message": (
                f"查询成功：{today} 入库 {data.get('inbound', 0)} 件，"
                f"出库 {data.get('outbound', 0)} 件，"
                f"当前库存总量 {data.get('total_stock', 0)} 件"
            ),
        }

    # ── 可选：批次查询 ──

    def query_batch(self, batch_no):
        data = self.http_get("/batches/detail", params={
            "batch_no": batch_no,
            "warehouse": self.warehouse_id,
        })
        if "error" in data or not data.get("batch"):
            return {
                "success": False,
                "error": "batch_not_found",
                "message": f"未找到批次 {batch_no}",
            }
        b = data["batch"]
        return {
            "success": True,
            "batch": b,
            "message": f"批次 {batch_no}：{b.get('item_name')}，余量 {b.get('quantity')} 件",
        }

    # ── 可选：批次移库 ──

    def move_batch_location(self, batch_no, new_location, quantity=None,
                            from_location=None, product_name=None,
                            operator="MCP系统"):
        payload = {
            "batch_no": batch_no,
            "target_location": new_location,
            "operator": operator,
            "warehouse": self.warehouse_id,
        }
        if quantity is not None:
            payload["quantity"] = quantity   # 不传 = 整批移

        result = self.http_post("/batches/move", data=payload)
        if "error" in result:
            return {"success": False, "error": result["error"], "message": f"移库失败: {result['error']}"}

        return {
            "success": True,
            "operation": "move_batch_location",
            "moved_quantity": result.get("moved", quantity or 0),
            "source_batch": result.get("source"),
            "target_batch": result.get("target"),
            "message": f"已将批次 {batch_no} 移至 {new_location}",
        }
```

对应的 `config.yml`：

```yaml
provider: "acme_wms"
api_base_url: "https://acme-wms.example.com/api/v1"
auth:
  type: bearer
  token: "eyJhbGciOiJIUzI1NiIs..."
timeout: 15
warehouse_id: "WH-SHENZHEN-01"
```

## 上传托管时的额外约束

如果你不是自己部署 MCP，而是把 `.py` 上传到我们的系统（[mcp/README.md](../mcp/README.md) 的路径 B），文件还要通过 AST 安全扫描：

- 文件 ≤ 100 KB，扩展名 `.py`
- 禁止导入：`os` `sys` `subprocess` `shutil` `socket` `ctypes` `code` `codeop`
- 禁止调用：`eval` `exec` `compile` `open` `__import__`
- **禁用调用是按函数名匹配的，含属性调用** —— `re.compile(...)` 会被判 `*.compile()` 违规，即使 `re` 在白名单里。改用 `re.match` / `re.search` 直接调
- 必须有一个 `BaseProvider` 子类且 `PROVIDER_NAME` 非空
- 必须实现上述 6 个必需方法

因为禁用了 `os`，所有配置都从构造函数的 `config` 读，不要读环境变量或文件。

完整的上传 → L1/L2 测试 → 激活 → 切模式流程见 [mcp/README.md §3.3](../mcp/README.md)。
外部作用域绑定、身份导入、人脸作用域这几步见同文档 §3.5–§3.8。

## 对接真实 ERP 时的两个坑

这两条都来自现场事故，代价是客户停用一天、以及一笔货入到了错误的型号上。

### 1. 对方可能要求「key 必须存在」，而不是「有值才发」

写请求时的常见习惯是「这个字段可选、没值就不放进 params」。有的 ERP（尤其
.NET/EF 那一挂）会对缺失的 key 直接做字符串操作，于是抛
`NullReferenceException` —— 中文环境下报文是「未将对象引用设置到对象的实例」。

现场那套备品系统的实测契约与它自己的接口文档并不一致：

| 接口 | 实测要求 |
|---|---|
| `parts_query_stock` | `partType` / `partNo` / `partName` **三个 key 都必须存在**，值可以是空字符串；三个全空即「拉全量」 |
| `parts_stock_in` | 除文档必填项外还要带 `location` 与 `UnitPrice` |

排查特征：**同一个错误串出现在所有接口上**，且响应极快（几毫秒，说明没走到
数据库）。给写接口发**空 params** 时如果回的不是「缺少必填字段」而是同一个
空引用异常，基本可以断定异常发生在参数校验之前。

写法上的建议是构造一个恒定形状的 params，只填要查的那个：

```python
@staticmethod
def _query_params(**kw) -> dict:
    params = {"partType": "", "partNo": "", "partName": ""}
    params.update({k: v for k, v in kw.items() if v is not None})
    return params
```

**对接新 ERP 时务必先手工探一遍参数矩阵**（逐个删 key、逐个改空值），别照着
对方文档想当然。文档说"选填"不等于实现能容忍缺失。

### 2. 同名不同规格：精确命中也必须先看有没有并列

`LocalMatchMixin` 已经处理好了，**自己写匹配逻辑时才需要当心**。错误写法：

```python
top_score, top = ranked[0]
if top_score >= 0.999:      # ← 跳过了下面的并列检查
    return top, None
tied = [x for x in ranked if abs(x[0] - top_score) < 0.02]
if len(tied) > 1:
    return ask_user(...)
```

现场一个「探针」对应 4 个型号，按名称查时每条都是名称精确匹配、都得 1.0 分，
于是静默取排第一的那条。**用户说型号 A 入库 5 个，货记到了型号 B 上。**
写操作尤其危险 —— 查询错了用户还能听出来，入库错了要盘点才发现。

正确顺序是并列判断在前：

```python
tied = [x for x in ranked if abs(x[0] - top_score) < 0.02]
if top_score >= 0.999 and len(tied) == 1:   # 精确命中「且唯一」才直达
    return top, None
```

**优先直接继承 `LocalMatchMixin`**（见 `mcp/providers/matching.py` 与参考实现
`tests/fixtures/mock_wms/provider.py`），它同时给你缓存、预计算索引、并列澄清
和「未命中即刷新」，不用自己踩一遍。

## 调试技巧

### 单独测试 Provider

无需启动完整的 MCP 链路，直接在 Python 中测试：

```python
import yaml
from providers import load_provider

with open("config.yml") as f:
    config = yaml.safe_load(f)

provider = load_provider(config)

# 测试各方法
print(provider.resolve_name("螺丝"))
print(provider.query_stock("M3螺丝"))
print(provider.get_today_statistics())
```

### 跑一遍连通性测试

```bash
cd mcp
uv run python -c "
from providers.test_runner import run_level1_tests, run_level2_tests
print(run_level1_tests('providers/custom/my_wms.py', {'api_base_url': 'https://...'}))
print(run_level2_tests('providers/custom/my_wms.py', {'api_base_url': 'https://...'}))
"
```

L2 会真的往你的 WMS 里写 `test_item` 各 1 件，**请指向测试环境**。

### 日志级别

```bash
export LOG_LEVEL=DEBUG
./start_mcp.sh
```

### 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `未知的 provider 'xxx'` | PROVIDER_NAME 与 config.yml 不匹配 | 检查拼写，确保 `.py` 在 `mcp/providers/` 或 `providers/custom/` 目录下 |
| 查询和入库都正常，**只有出库 TypeError** | `stock_out` 漏了 `allow_partial_fallback` 参数 | 按本文签名补齐；工具层无条件按关键字传它 |
| `TypeError: missing ... 'fuzzy'` | 用了旧的 `reason` 单参签名 | `reason` 已拆成 `reason_category` + `reason_note`，共 6 个必需位置参数 |
| 某工具恒返回 `not_implemented` | 没实现 `query_batch` / `move_batch_location` | 见「2 个可选方法」 |
| `无法连接到后端服务` | `api_base_url` 不可达 | 检查 URL、网络和防火墙 |
| `401 Unauthorized` | 认证配置错误 | 检查 `auth` 块的 type 和凭证 |
| 所有工具返回 `face_auth_denied:http_404` | `api_base_url` 指向的后端没有 `/face/verify-mcp` | 见 [mcp/README.md §2.5](../mcp/README.md) |
| L1 四个方法一起失败，error 是「Provider 加载失败」 | 构造函数抛异常 | 用 `config.get(k, default)` 而不是 `config[k]` |
| 方法返回空结果 | 外部 WMS API 响应格式不匹配 | 用日志打印原始响应，对照 API 文档调整字段映射 |
| 返回长列表时连接被断（1009） | 单帧超过约 13 KB | 裁剪 `items`，收紧 `max_results` |

---

<a name="english"></a>

# WMS Provider Development Guide

English | [中文](#wms-provider-开发指南)

This guide explains how to write a custom Provider to connect MCP voice control to your own WMS backend, **without modifying any MCP tool code**.

> 📖 **Read [mcp/README_EN.md](../mcp/README_EN.md) first** — it covers the two integration paths, the face gate, and the upload validation flow. This document covers the **Provider interface contract** only.
>
> ⚠️ **`mcp/providers/base.py` is the source of truth for signatures.** This document was realigned to the current code in 2026-08.

## Architecture

```
Watcher Voice → MCP Endpoint → warehouse_mcp.py → Provider → Your WMS API
                                    │
                                    ├── DefaultProvider  (built-in backend)
                                    ├── YourWmsProvider   (your WMS)
                                    └── ...               (more)
```

The MCP tool layer (`warehouse_mcp.py`) communicates with backends through the Provider interface. To switch WMS:

1. Create a new `.py` file in `mcp/providers/` (or `providers/custom/` for hosted uploads)
2. Extend `BaseProvider` and implement 6 required methods (2 more are optional)
3. Set `provider` name in `config.yml`

The system auto-discovers all Providers in `providers/` and `providers/custom/` — no manual registration needed.

## Quick Start

### 1. Create Provider File

```python
"""Provider for MyWMS system"""

from .base import BaseProvider


class MyWmsProvider(BaseProvider):
    """MyWMS backend adapter."""

    PROVIDER_NAME = "my_wms"   # matches config.yml provider field

    # ── 6 required methods ──

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

    # ── 2 optional methods (tools return not_implemented if omitted) ──

    def query_batch(self, batch_no): ...

    def move_batch_location(self, batch_no, new_location, quantity=None,
                            from_location=None, product_name=None,
                            operator="MCP系统"): ...
```

> ⚠️ **Parameter order is fixed.** Both the tool layer and the connectivity tests pass arguments **positionally**. You may rename parameters; reordering them is a TypeError. Watch `stock_out`'s `allow_partial_fallback` in particular — the tool layer always passes it as a keyword, so omitting it makes **every outbound call fail**.

### 2. Update Config

```yaml
provider: "my_wms"
api_base_url: "https://your-wms.example.com/api"
auth:
  type: bearer
  token: "your-access-token"
timeout: 15
```

> If you also use our face recognition, `api_base_url` must stay pointed at our backend and your WMS address goes in a custom field. See [mcp/README_EN.md §2.5](../mcp/README_EN.md).

### 3. Start

```bash
cd mcp
./start_mcp.sh
```

Log output shows `使用 provider: my_wms (MyWmsProvider)` to confirm the switch.

## BaseProvider Interface

### Constructor

`config` contains the full `config.yml` content (merged with the Provider's DB `config` JSON for hosted uploads). Add custom fields as needed:

```yaml
provider: "my_wms"
api_base_url: "https://your-wms.example.com/api"
warehouse_id: "WH-001"       # custom field
```

```python
def __init__(self, config: dict):
    super().__init__(config)
    self.warehouse_id = config.get("warehouse_id", "")
```

> Always use `config.get(k, default)`, never `config[k]`. A constructor exception makes the whole connectivity test report "Provider load failed" with all four methods red at once — hard to diagnose.

### Built-in HTTP Helpers

The base class provides `http_get` and `http_post` with automatic auth headers and error handling:

```python
data = self.http_get("/items", params={"sku": "ABC"})
result = self.http_post("/inbound", data={"sku": "ABC", "qty": 10})
```

They return `{"error": "..."}` instead of raising, so check `"error" in data` in every method.

The base class reads `config["api_base_url"]` into `self.base_url`; override it in `__init__` to point HTTP calls elsewhere.

### Authentication

| type | Fields | Generated Header |
|------|--------|-----------------|
| `api_key` | `key`, `header` (optional, default `X-API-Key`) | `X-API-Key: <key>` |
| `bearer` | `token` | `Authorization: Bearer <token>` |
| `basic` | `username`, `password` | `Authorization: Basic <base64>` |
| `custom` | — | Override `get_auth_headers()` in subclass |

## Required Methods

Each method must return a `dict`. See the Chinese section above for full return-format specs.

| Method | Purpose | Required return keys |
|--------|---------|------------------|
| `resolve_name(text, entity_type)` | Fuzzy name resolution | `best_match`, `confident` |
| `query_stock(product_name, show_batches)` | Query inventory | `success` |
| `stock_in(...)` | Record inbound | `success` |
| `stock_out(...)` | Record outbound | `success` |
| `search(...)` | Unified search | `success`, `items` |
| `get_today_statistics()` | Daily summary | `success`, `statistics` |

Key argument semantics:

| Argument | Meaning |
|---|---|
| `reason_category` (in) | `purchase` \| `return` \| `refund` \| `produce` \| `transfer_in` \| `other_in` |
| `reason_category` (out) | `sell` \| `lend` \| `consume` \| `loss` \| `transfer_out` \| `other_out` |
| `reason_note` | Free-text note; may be `""` or `None` |
| `operator` | LLM/device-supplied, defaults to `"MCP系统"` — **untrusted** (forgeable via prompt injection) |
| `actual_operator` | Name snapshot from face verification — **trusted**; `None` when face is off |
| `batch_no` | When set, deduct **only** from that batch; fail rather than fall back |
| `allow_partial_fallback` | Whether an insufficient batch/location may draw from other stock. Defaults to `False`; the tool layer asks the user first and resends with `True` |

`get_today_statistics()` doubles as the health probe for `GET /api/erp/providers/{id}/status` — keep it lightweight.

## Optional Methods

`query_batch(batch_no)` and `move_batch_location(...)` have default implementations returning a structured `not_implemented`, so a Provider without them still instantiates — but the corresponding MCP tools will always fail. New Providers should implement both. See the Chinese section for return formats.

## External Scope Discovery (optional; needed for multi-warehouse / multi-org)

Once an external ERP is bound, our tenants/warehouses have **no relationship** to
yours. Rather than mirroring your org structure locally (which guarantees dual
maintenance and drift), we invert it: the Provider reports what *your* system has,
the user picks it while configuring an agent, and we store the **raw codes** and
pass them back to you verbatim on every call.

Both methods are optional (the base class returns `not_implemented`). Implement
whichever match your system's shape:

| Your system | Implement | What the config UI shows |
|---|---|---|
| Single org, single warehouse | neither | Leave both blank; calls use the fixed values in your Provider config |
| Single org, multiple warehouses | `list_warehouses` only | Tenant degrades to a text box (leave empty), warehouse is a dropdown |
| Multiple orgs and warehouses | both | Two-level cascade: pick org, then a warehouse within it |

When only one candidate is returned, the UI auto-selects it — no user action needed.

```python
def list_tenants(self) -> dict:
    return {"success": True,
            "items": [{"id": "ORG-BJ", "name": "Beijing HQ"}],
            "message": "ok"}

def list_warehouses(self, tenant_id=None) -> dict:
    # tenant_id is the org code the user selected; ignore it if you have no orgs
    return {"success": True,
            "items": [{"id": "WH-01", "name": "Main Warehouse"}],
            "message": "ok"}
```

`items[].id` is the code stored and handed back to you; `name` is display-only.
On failure return `{"success": False, "error": "...", "items": [], "message": "..."}`
— the UI falls back to manual entry instead of blocking configuration.

### `list_users(tenant_id=None) -> dict` (optional)

Different purpose from tenant/warehouse discovery: **authorization stays on our
side and cannot be delegated.** Who may log in, configure which agent, or change
face rules is decided by our `users(role, tenant_id)` + `user_warehouses` chain.
So even though inventory lives entirely in your system, the "user → tenant/role"
mapping must exist on ours. This method just saves an admin from retyping your
user table by hand.

```python
def list_users(self, tenant_id=None):
    return {"success": True,
            "items": [{"id": "u1001", "name": "zhangsan", "display_name": "Zhang San"}],
            "message": "ok"}
```

`items[].id` is stored as `users.external_user_id` (used for incremental sync and dedup),
`name` becomes the login, `display_name` the display name, and `warehouses` is turned into
warehouse grants on our side. **Passwords are managed locally on our side** — you do not need
to expose any authentication endpoint.

Supplying `warehouses` is strongly recommended: for non-admin roles we honour only explicit
grants, so without it an imported user logs in to an empty warehouse list. The codes must
already exist as local anchors (import warehouses first); unmatched ones are reported back in
`unmatched_warehouses` rather than dropped silently. The `admin` role gets no per-warehouse
grants — it can already see every warehouse in its tenant.

Import is idempotent: re-importing the same `external_user_id` updates instead of
duplicating; a local user with the same name but a different external id is
skipped and reported rather than silently overwritten (this notably protects the
local admin account).

> **Imported users carry permissions only** — they take no part in the business
> flow. An imported user is neither the stock-movement `operator` (free-form text)
> nor a face-library subject (enrolled separately). Users exist solely to decide
> who may change these configurations. Do not build implicit links between the three.

Read the user's selection from `config` in `__init__`:

```python
def __init__(self, config: dict):
    super().__init__(config)
    self.tenant_id = config.get("external_tenant_id") or config.get("tenant_id")
    self.warehouse_id = config.get("external_warehouse_id") or config.get("warehouse_id", "default")
```

There is **one Provider instance per agent**, so agents bound to different
warehouses are isolated automatically — no need to branch inside your methods.

## Hosted Upload Constraints

If you upload the `.py` to our system rather than self-hosting, it must also pass an AST security scan:

- ≤ 100 KB, `.py` extension
- Forbidden imports: `os` `sys` `subprocess` `shutil` `socket` `ctypes` `code` `codeop`
- Forbidden calls: `eval` `exec` `compile` `open` `__import__`
- **Forbidden calls match by function name, including attribute calls** — `re.compile(...)` is flagged as `*.compile()` even though `re` is whitelisted. Use `re.match` / `re.search` directly
- Must contain a `BaseProvider` subclass with a non-empty `PROVIDER_NAME`
- Must implement the 6 required methods

Since `os` is forbidden, read all configuration from the constructor's `config` dict.

## Debugging

Test your Provider standalone without the full MCP stack:

```python
import yaml
from providers import load_provider

with open("config.yml") as f:
    config = yaml.safe_load(f)

provider = load_provider(config)
print(provider.query_stock("M3 Screw"))
print(provider.get_today_statistics())
```

Run the connectivity tests directly:

```bash
cd mcp
uv run python -c "
from providers.test_runner import run_level1_tests, run_level2_tests
print(run_level1_tests('providers/custom/my_wms.py', {'api_base_url': 'https://...'}))
print(run_level2_tests('providers/custom/my_wms.py', {'api_base_url': 'https://...'}))
"
```

L2 really writes 1 unit of `test_item` into your WMS — **point it at a test environment**.

### Common Issues

| Issue | Cause | Fix |
|------|------|---------|
| Queries and inbound work, **only outbound raises TypeError** | `stock_out` is missing `allow_partial_fallback` | Add it; the tool layer always passes it as a keyword |
| `TypeError: missing ... 'fuzzy'` | Using the old single-`reason` signature | `reason` split into `reason_category` + `reason_note` — 6 required positional args |
| A tool always returns `not_implemented` | `query_batch` / `move_batch_location` not implemented | See Optional Methods |
| L1 fails on all four methods with "Provider load failed" | Constructor raised | Use `config.get(k, default)` |
| All tools return `face_auth_denied:http_404` | Backend at `api_base_url` has no `/face/verify-mcp` | See [mcp/README_EN.md §2.5](../mcp/README_EN.md) |
| Connection drops on long lists (1009) | Frame exceeded ~13 KB | Trim `items`, lower `max_results` |
