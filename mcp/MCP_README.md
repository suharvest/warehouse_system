# 仓库管理系统 MCP 接口文档

[English](MCP_README_EN.md) | 中文

## 概述

本 MCP 服务器为仓库管理系统提供了 API 接口，专门用于管理 watcher-xiaozhi 产品的库存。

## MCP 工具列表

### 1. get_today_statistics - 查询当天统计数据

查询当天的入库数量、出库数量和当前库存总量。

**参数：** 无

**返回示例：**
```json
{
  "success": true,
  "date": "2024-11-07",
  "statistics": {
    "today_in": 50,
    "today_out": 30,
    "total_stock": 3300,
    "low_stock_count": 5,
    "net_change": 20
  },
  "message": "查询成功：2024-11-07 入库 50 件，出库 30 件，当前库存总量 3300 件"
}
```

**字段说明：**
- `today_in`: 今日入库总数量
- `today_out`: 今日出库总数量
- `total_stock`: 当前库存总量
- `low_stock_count`: 库存预警数量（低于安全库存的物料种类数）
- `net_change`: 今日净变化（入库 - 出库）

### 2. query_xiaozhi_stock - 查询库存

查询指定 watcher-xiaozhi 产品的库存信息。

**参数：**
- `product_name` (string, 可选): 产品名称，默认为 "watcher-xiaozhi(标准版)"

**可选产品名称：**
- `watcher-xiaozhi(标准版)`
- `watcher-xiaozhi(专业版)`
- `watcher-xiaozhi整机`
- `watcher-xiaozhi主控板`
- `watcher-xiaozhi扩展板`
- `watcher-xiaozhi外壳(上)`
- `watcher-xiaozhi外壳(下)`

**返回示例：**
```json
{
  "success": true,
  "product": {
    "name": "watcher-xiaozhi(标准版)",
    "sku": "FG-WZ-STD",
    "quantity": 52,
    "unit": "台",
    "safe_stock": 15,
    "location": "H区-02",
    "status": "正常"
  },
  "message": "查询成功：watcher-xiaozhi(标准版) 当前库存 52 台"
}
```

### 3. stock_in - 入库操作

将 watcher-xiaozhi 产品入库。

**参数：**
- `product_name` (string, 必填): 产品名称
- `quantity` (integer, 必填): 入库数量（必须大于0）
- `reason` (string, 可选): 入库原因，默认为 "采购入库"
- `operator` (string, 可选): 操作人，默认为 "MCP系统"

**返回示例：**
```json
{
  "success": true,
  "operation": "stock_in",
  "product": {
    "name": "watcher-xiaozhi(标准版)",
    "old_quantity": 52,
    "in_quantity": 10,
    "new_quantity": 62,
    "unit": "台"
  },
  "message": "入库成功：watcher-xiaozhi(标准版) 入库 10 台，库存从 52 更新到 62 台"
}
```

### 4. stock_out - 出库操作

将 watcher-xiaozhi 产品出库。

**参数：**
- `product_name` (string, 必填): 产品名称
- `quantity` (integer, 必填): 出库数量（必须大于0）
- `reason` (string, 可选): 出库原因，默认为 "销售出库"
- `operator` (string, 可选): 操作人，默认为 "MCP系统"

**返回示例（成功）：**
```json
{
  "success": true,
  "operation": "stock_out",
  "product": {
    "name": "watcher-xiaozhi(标准版)",
    "old_quantity": 62,
    "out_quantity": 5,
    "new_quantity": 57,
    "unit": "台",
    "safe_stock": 15
  },
  "message": "出库成功：watcher-xiaozhi(标准版) 出库 5 台，库存从 62 更新到 57 台",
  "warning": ""
}
```

**返回示例（库存不足）：**
```json
{
  "success": false,
  "error": "库存不足",
  "message": "出库失败：watcher-xiaozhi(标准版) 库存不足，当前库存 5 台，需要出库 10 台"
}
```

**返回示例（库存告急警告）：**
```json
{
  "success": true,
  "operation": "stock_out",
  "product": {
    "name": "watcher-xiaozhi(标准版)",
    "old_quantity": 10,
    "out_quantity": 5,
    "new_quantity": 5,
    "unit": "台",
    "safe_stock": 15
  },
  "message": "出库成功：watcher-xiaozhi(标准版) 出库 5 台，库存从 10 更新到 5 台",
  "warning": "⚠️ 警告：库存告急！当前库存 5 台，低于安全库存 15 台 的50%"
}
```

### 5. list_xiaozhi_products - 列出所有产品

列出所有 watcher-xiaozhi 相关产品的库存信息。

**参数：** 无

**返回示例：**
```json
{
  "success": true,
  "count": 7,
  "products": [
    {
      "id": 1,
      "name": "watcher-xiaozhi主控板",
      "sku": "MB-WZ-001",
      "quantity": 95,
      "unit": "个",
      "safe_stock": 30,
      "location": "A区-01"
    },
    ...
  ],
  "message": "查询成功，共找到 7 种 watcher-xiaozhi 相关产品"
}
```

## 配置方法

### 1. 在 Claude Desktop 中配置

编辑 Claude Desktop 的配置文件，添加以下内容：

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "warehouse-system": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "warehouse_mcp.py"],
      "cwd": "/Users/harvest/project/test_dataset/warehouse_system/mcp"
    }
  }
}
```

**注意：** 请将 `cwd` 路径修改为你的实际项目的 mcp 目录路径。

### 2. 重启 Claude Desktop

配置完成后，重启 Claude Desktop 使配置生效。

### 3. 验证 MCP 工具

在 Claude Desktop 中，你应该能看到以下工具：
- get_today_statistics
- query_xiaozhi_stock
- stock_in
- stock_out
- list_xiaozhi_products

## 使用示例

### 示例 1：查询今日统计数据

**请求：**
```
请查询今天的仓库统计数据
```

**MCP 调用：**
```python
get_today_statistics()
```

**返回：**
```json
{
  "success": true,
  "date": "2024-11-07",
  "statistics": {
    "today_in": 50,
    "today_out": 30,
    "total_stock": 3300,
    "low_stock_count": 5,
    "net_change": 20
  },
  "message": "查询成功：2024-11-07 入库 50 件，出库 30 件，当前库存总量 3300 件"
}
```

### 示例 2：查询标准版库存

**请求：**
```
请查询 watcher-xiaozhi(标准版) 的库存
```

**MCP 调用：**
```python
query_xiaozhi_stock(product_name="watcher-xiaozhi(标准版)")
```

### 示例 3：入库 10 台标准版

**请求：**
```
请为 watcher-xiaozhi(标准版) 入库 10 台，原因是新采购到货
```

**MCP 调用：**
```python
stock_in(
    product_name="watcher-xiaozhi(标准版)",
    quantity=10,
    reason="新采购到货",
    operator="采购部"
)
```

### 示例 4：出库 5 台标准版

**请求：**
```
watcher-xiaozhi(标准版) 销售出库 5 台
```

**MCP 调用：**
```python
stock_out(
    product_name="watcher-xiaozhi(标准版)",
    quantity=5,
    reason="销售出库",
    operator="销售部"
)
```

### 示例 5：列出所有产品

**请求：**
```
列出所有 watcher-xiaozhi 产品
```

**MCP 调用：**
```python
list_xiaozhi_products()
```

## 前端界面实时更新

MCP 操作完成后，前端界面（http://localhost:2125）会在3秒内自动更新，显示最新的库存数据。

### 多语言支持

前端界面支持中英文切换：
- 点击右上角语言下拉菜单
- 选择 "中文简体" 或 "English"
- 页面即时切换语言

### 更新机制

1. MCP 工具直接修改数据库
2. 前端每3秒自动刷新库存列表
3. 无需手动刷新页面即可看到变化

### 验证更新

执行以下操作来验证：

1. **打开前端页面**
   ```
   http://localhost:2125
   ```

2. **在搜索框输入** "标准版" 查看当前库存

3. **通过 Claude Desktop 调用 MCP**
   ```
   请为 watcher-xiaozhi(标准版) 入库 5 台
   ```

4. **观察前端页面**
   - 等待最多3秒
   - 库存数量会自动更新
   - 如果低于安全库存，状态标签会变化

## 错误处理

### 常见错误

1. **产品不存在**
   ```json
   {
     "success": false,
     "error": "未找到产品: xxx",
     "message": "产品 'xxx' 不存在，请检查产品名称"
   }
   ```

2. **库存不足**
   ```json
   {
     "success": false,
     "error": "库存不足",
     "message": "出库失败：库存不足..."
   }
   ```

3. **数量无效**
   ```json
   {
     "success": false,
     "error": "入库数量必须大于0",
     "message": "入库失败：数量 -5 无效"
   }
   ```

## 测试 MCP 服务器

### 方法 1：使用测试脚本

```bash
# 从项目根目录运行
python3 test/test_mcp.py

# 或从 test 目录运行
cd test
python3 test_mcp.py
```

### 方法 2：使用 MCP Inspector

```bash
cd /Users/harvest/project/test_dataset/warehouse_system/mcp
npx @modelcontextprotocol/inspector uv run python warehouse_mcp.py
```

### 方法 3：直接运行

```bash
cd /Users/harvest/project/test_dataset/warehouse_system/mcp
uv run python warehouse_mcp.py
```

## 日志

MCP 服务器会记录所有操作日志，包括：
- 查询操作
- 入库操作（产品名称、数量、操作人）
- 出库操作（产品名称、数量、操作人、剩余库存）
- 错误信息

## 安全注意事项

1. MCP 工具直接操作数据库，请谨慎使用
2. 建议定期备份数据库文件 `backend/warehouse.db`
3. 出库操作会自动检查库存是否足够
4. 入库/出库数量必须大于0

## 技术细节

- **使用框架**: FastMCP
- **传输方式**: stdio
- **数据库**: SQLite
- **Python 版本**: 3.12+
- **依赖**: fastmcp, mcp

## 故障排除

### MCP 工具无法使用

1. 检查 Claude Desktop 配置是否正确
2. 确认项目路径是否正确
3. 重启 Claude Desktop
4. 检查是否安装了 `uv` 和相关依赖

### 数据不同步

1. 确认后端服务是否运行（端口 2124）
2. 前端页面是否正常刷新（每3秒）
3. 检查数据库文件是否存在

### 操作失败

1. 检查产品名称是否正确（区分大小写）
2. 确认数量是否大于0
3. 出库时检查库存是否足够
4. 查看 MCP 返回的错误信息

## 联系与支持

如有问题，请查看：
- `README.md` - 项目说明
- `USAGE.md` - 使用文档
- `UPDATE_LOG.md` - 更新日志
