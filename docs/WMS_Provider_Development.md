# WMS Provider 开发指南

[English](#english) | 中文

本文档说明如何编写自定义 Provider，让 MCP 语音控制对接你自己的 WMS（仓库管理系统）后端，**无需修改任何 MCP 工具代码**。

## 架构概览

```
Watcher 语音 → MCP Endpoint → warehouse_mcp.py → Provider → 你的 WMS API
                                    │
                                    ├── DefaultProvider  (自有后端)
                                    ├── YourWmsProvider   (你的 WMS)
                                    └── ...               (更多)
```

MCP 工具层（`warehouse_mcp.py`）通过 Provider 接口与后端通信。切换 WMS 只需：

1. 在 `mcp/providers/` 目录新建一个 `.py` 文件
2. 继承 `BaseProvider`，实现 6 个方法
3. 在 `config.yml` 中指定 `provider` 名称

系统会自动扫描 `providers/` 目录，发现并注册所有 Provider，无需手动注册。

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

    def resolve_name(self, text, entity_type="all"):
        ...

    def query_stock(self, product_name, show_batches=False):
        ...

    def stock_in(self, product_name, quantity, reason, operator, fuzzy,
                 location=None, contact_id=None):
        ...

    def stock_out(self, product_name, quantity, reason, operator, fuzzy):
        ...

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        ...

    def get_today_statistics(self):
        ...
```

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

`config` 是 `config.yml` 的完整内容。你可以在其中添加自定义字段：

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

### 内置 HTTP 工具

基类提供了 `http_get` 和 `http_post` 方法，自动处理认证头和错误：

```python
# GET 请求
data = self.http_get("/inventory/items", params={"sku": "ABC123"})

# POST 请求
result = self.http_post("/inventory/inbound", data={"sku": "ABC123", "qty": 10})
```

如果你的 WMS API 格式与默认不同，可以 override 这两个方法或 `get_auth_headers()`。

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

## 6 个必须实现的方法

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

**实现建议：** 如果外部 WMS 有搜索 API，直接调用即可。如果没有，可以拉取物料列表后用 `rapidfuzz` 本地匹配。

### 2. `query_stock(product_name, show_batches) -> dict`

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

### 3. `stock_in(product_name, quantity, reason, operator, fuzzy, location, contact_id) -> dict`

产品入库。

**返回格式：**

```python
{
    "success": True,
    "message": "入库成功：M3螺丝 入库 100 个",
    "product_name": "M3螺丝",
    "quantity": 100,
    "new_stock": 600,
}
```

### 4. `stock_out(product_name, quantity, reason, operator, fuzzy) -> dict`

产品出库。返回格式同 `stock_in`。

### 5. `search(query, entity_type, category, status, contact_type, fuzzy, include_batches, max_results) -> dict`

统一搜索。

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


class AcmeWmsProvider(BaseProvider):
    """AcmeWMS 后端适配器。"""

    PROVIDER_NAME = "acme_wms"

    def __init__(self, config: dict):
        super().__init__(config)
        self.warehouse_id = config.get("warehouse_id", "default")

    # ── 1. 模糊名称解析 ──

    def resolve_name(self, text, entity_type="all"):
        # AcmeWMS 有内置的模糊搜索 API
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

    def stock_in(self, product_name, quantity, reason, operator, fuzzy,
                 location=None, contact_id=None):
        payload = {
            "item_name": product_name,
            "quantity": quantity,
            "reason": reason,
            "operator": operator,
            "warehouse": self.warehouse_id,
        }
        if location:
            payload["location"] = location

        result = self.http_post("/inventory/inbound", data=payload)
        if "error" in result:
            return {"success": False, "error": result["error"], "message": f"入库失败: {result['error']}"}

        return {
            "success": True,
            "message": f"入库成功：{product_name} 入库 {quantity} 件",
            "product_name": product_name,
            "quantity": quantity,
            "new_stock": result.get("new_quantity", 0),
        }

    # ── 4. 出库 ──

    def stock_out(self, product_name, quantity, reason, operator, fuzzy):
        result = self.http_post("/inventory/outbound", data={
            "item_name": product_name,
            "quantity": quantity,
            "reason": reason,
            "operator": operator,
            "warehouse": self.warehouse_id,
        })
        if "error" in result:
            return {"success": False, "error": result["error"], "message": f"出库失败: {result['error']}"}

        return {
            "success": True,
            "message": f"出库成功：{product_name} 出库 {quantity} 件",
            "product_name": product_name,
            "quantity": quantity,
            "new_stock": result.get("new_quantity", 0),
        }

    # ── 5. 搜索 ──

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        params = {"type": entity_type, "limit": max_results or 30}
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

### 日志级别

设置环境变量查看详细日志：

```bash
export LOG_LEVEL=DEBUG
./start_mcp.sh
```

### 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `未知的 provider 'xxx'` | PROVIDER_NAME 与 config.yml 不匹配 | 检查拼写，确保 `.py` 文件在 `mcp/providers/` 目录下 |
| `无法连接到后端服务` | `api_base_url` 不可达 | 检查 URL、网络和防火墙 |
| `401 Unauthorized` | 认证配置错误 | 检查 `auth` 块的 type 和凭证 |
| 方法返回空结果 | 外部 WMS API 响应格式不匹配 | 用日志打印原始响应，对照 API 文档调整字段映射 |

---

<a name="english"></a>

# WMS Provider Development Guide

English | [中文](#wms-provider-开发指南)

This guide explains how to write a custom Provider to connect MCP voice control to your own WMS backend, **without modifying any MCP tool code**.

## Architecture

```
Watcher Voice → MCP Endpoint → warehouse_mcp.py → Provider → Your WMS API
                                    │
                                    ├── DefaultProvider  (built-in backend)
                                    ├── YourWmsProvider   (your WMS)
                                    └── ...               (more)
```

The MCP tool layer (`warehouse_mcp.py`) communicates with backends through the Provider interface. To switch WMS:

1. Create a new `.py` file in `mcp/providers/`
2. Extend `BaseProvider` and implement 6 methods
3. Set `provider` name in `config.yml`

The system auto-discovers all Providers in the `providers/` directory — no manual registration needed.

## Quick Start

### 1. Create Provider File

Create a new file in `mcp/providers/`, e.g., `my_wms.py`:

```python
"""Provider for MyWMS system"""

from .base import BaseProvider


class MyWmsProvider(BaseProvider):
    """MyWMS backend adapter."""

    PROVIDER_NAME = "my_wms"   # matches config.yml provider field

    def resolve_name(self, text, entity_type="all"):
        ...

    def query_stock(self, product_name, show_batches=False):
        ...

    def stock_in(self, product_name, quantity, reason, operator, fuzzy,
                 location=None, contact_id=None):
        ...

    def stock_out(self, product_name, quantity, reason, operator, fuzzy):
        ...

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        ...

    def get_today_statistics(self):
        ...
```

### 2. Update Config

Edit `mcp/config.yml`:

```yaml
provider: "my_wms"
api_base_url: "https://your-wms.example.com/api"
auth:
  type: bearer
  token: "your-access-token"
timeout: 15
```

### 3. Start

```bash
cd mcp
./start_mcp.sh
```

Log output will show `使用 provider: my_wms (MyWmsProvider)` to confirm the switch.

## BaseProvider Interface

### Constructor

`config` contains the full `config.yml` content. Add custom fields as needed:

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

### Built-in HTTP Helpers

The base class provides `http_get` and `http_post` with automatic auth headers and error handling:

```python
data = self.http_get("/items", params={"sku": "ABC"})
result = self.http_post("/inbound", data={"sku": "ABC", "qty": 10})
```

### Authentication

Configured in the `auth` block of `config.yml`:

| type | Fields | Generated Header |
|------|--------|-----------------|
| `api_key` | `key`, `header` (optional, default `X-API-Key`) | `X-API-Key: <key>` |
| `bearer` | `token` | `Authorization: Bearer <token>` |
| `basic` | `username`, `password` | `Authorization: Basic <base64>` |
| `custom` | — | Override `get_auth_headers()` in subclass |

## Required Methods

Each method must return a `dict`. See the Chinese section above for detailed return format specifications for all 6 methods:

| Method | Purpose | Key Return Fields |
|--------|---------|------------------|
| `resolve_name(text, entity_type)` | Fuzzy name resolution | `best_match`, `confident`, `candidates` |
| `query_stock(product_name, show_batches)` | Query inventory | `success`, `product`, `message` |
| `stock_in(...)` | Record inbound | `success`, `message`, `new_stock` |
| `stock_out(...)` | Record outbound | `success`, `message`, `new_stock` |
| `search(...)` | Unified search | `success`, `count`, `total`, `items` |
| `get_today_statistics()` | Daily summary | `success`, `date`, `statistics` |

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
