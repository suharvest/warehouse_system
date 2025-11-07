# Claude Desktop MCP 配置指南

## 快速配置

### 1. 找到配置文件

**macOS:**
```bash
~/Library/Application Support/Claude/claude_desktop_config.json
```

**Windows:**
```
%APPDATA%\Claude\claude_desktop_config.json
```

### 2. 添加配置

编辑配置文件，添加以下内容：

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

**⚠️ 重要：** 请将 `cwd` 路径改为你的实际项目路径！

### 3. 获取项目路径

在项目的 mcp 目录运行：
```bash
cd /path/to/warehouse_system/mcp
pwd
```

将输出的完整路径复制到配置文件的 `cwd` 字段。

### 4. 重启 Claude Desktop

配置完成后，完全退出并重新打开 Claude Desktop。

### 5. 验证工具是否可用

在 Claude Desktop 中输入：
```
请列出所有 watcher-xiaozhi 产品
```

如果看到产品列表，说明 MCP 工具已正确配置！

## 示例配置（多个MCP服务器）

如果你已经配置了其他 MCP 服务器，添加到现有配置中：

```json
{
  "mcpServers": {
    "calculator": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "calculator"]
    },
    "warehouse-system": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "warehouse_mcp.py"],
      "cwd": "/Users/harvest/project/test_dataset/warehouse_system/mcp"
    }
  }
}
```

## 故障排除

### 工具不可用

1. 检查配置文件路径是否正确
2. 确认 `cwd` 指向项目根目录
3. 确认已安装 `uv`：
   ```bash
   uv --version
   ```
4. 完全退出并重启 Claude Desktop（不是刷新）

### 数据库错误

1. 确认数据库文件存在：
   ```bash
   ls backend/warehouse.db
   ```
2. 如果不存在，运行初始化：
   ```bash
   cd backend
   uv run python database.py
   ```

### 依赖缺失

```bash
cd /Users/harvest/project/test_dataset/warehouse_system
uv add fastmcp mcp
```

## 测试 MCP 工具

### 方法 1：在 Claude Desktop 中测试

```
查询 watcher-xiaozhi(标准版) 的库存
```

### 方法 2：使用测试脚本

```bash
cd /Users/harvest/project/test_dataset/warehouse_system
python3 test/test_mcp.py
```

### 方法 3：使用 MCP Inspector

```bash
cd /Users/harvest/project/test_dataset/warehouse_system
npx @modelcontextprotocol/inspector uv run python -m warehouse_mcp
```

## 常见使用场景

### 场景 1：查询库存
```
请查询 watcher-xiaozhi(标准版) 的当前库存
```

### 场景 2：入库操作
```
watcher-xiaozhi(标准版) 采购到货 20 台，请帮忙入库
```

### 场景 3：出库操作
```
销售了 5 台 watcher-xiaozhi(标准版)，请出库
```

### 场景 4：查看所有产品
```
列出所有 watcher-xiaozhi 相关产品及其库存
```

## 配置文件位置速查

### macOS
```bash
# 直接编辑
open -e "~/Library/Application Support/Claude/claude_desktop_config.json"

# 查看内容
cat "~/Library/Application Support/Claude/claude_desktop_config.json"
```

### Windows
```cmd
# 打开所在目录
explorer %APPDATA%\Claude

# 用记事本编辑
notepad %APPDATA%\Claude\claude_desktop_config.json
```

## 注意事项

1. **路径必须是绝对路径**：不能使用 `~` 或相对路径
2. **路径格式**：
   - macOS/Linux: `/Users/username/path/to/project`
   - Windows: `C:\\Users\\username\\path\\to\\project`
3. **JSON 格式**：确保所有括号、逗号、引号都正确
4. **重启必须**：修改配置后必须重启 Claude Desktop

## 验证清单

- [ ] 配置文件路径正确
- [ ] `cwd` 指向项目的 mcp 目录（包含 `warehouse_mcp.py` 的目录）
- [ ] 已安装 `uv` 和所有依赖
- [ ] 数据库文件存在 (`backend/warehouse.db`)
- [ ] 已完全重启 Claude Desktop
- [ ] 在 Claude Desktop 中可以看到工具提示
- [ ] 测试查询命令能正常工作

## 成功标志

配置成功后，在 Claude Desktop 中，当你输入与库存相关的问题时，Claude 会自动调用 MCP 工具，你会看到类似这样的响应：

```
我来查询 watcher-xiaozhi(标准版) 的库存。

[调用工具: query_xiaozhi_stock]

查询成功！watcher-xiaozhi(标准版) 当前库存 57 台，
位于 H区-02，库存状态正常。
```

## 获取帮助

如果遇到问题，请查看：
- `MCP_README.md` - 完整的 MCP 文档
- `README.md` - 项目说明
- `USAGE.md` - 使用文档

或运行测试脚本查看详细错误信息：
```bash
python3 test/test_mcp.py
```
