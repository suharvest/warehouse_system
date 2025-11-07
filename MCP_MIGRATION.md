# MCP 文件迁移说明

## 变更概述

所有 MCP 相关文件已统一移动到 `mcp/` 目录。

## 文件变更

### 之前的位置：
```
warehouse_system/
├── warehouse_mcp.py          # 根目录
├── mcp_config.json           # 根目录
├── mcp_pipe.py               # 根目录
└── ...
```

### 现在的位置：
```
warehouse_system/
├── mcp/
│   ├── warehouse_mcp.py      # MCP 服务器
│   ├── mcp_config.json       # MCP 配置
│   └── mcp_pipe.py           # MCP 管道
└── ...
```

## 代码修改

### 1. warehouse_mcp.py

**修改内容：** 更新了 backend 目录的路径引用

```python
# 之前
backend_dir = os.path.join(os.path.dirname(__file__), 'backend')

# 现在
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(project_root, 'backend')
```

**原因：** 文件从根目录移到 mcp 子目录，需要向上一级才能找到 backend 目录

### 2. start.sh

**修改内容：** 更新了 MCP 服务的启动命令

```bash
# 之前
uv run mcp_pipe.py warehouse_mcp.py &

# 现在
cd mcp
uv run python mcp_pipe.py warehouse_mcp.py &
cd ..
```

**原因：** 文件移到 mcp 目录，需要先切换目录再执行

## 配置文件更新

### Claude Desktop 配置

**文件位置：**
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

**之前的配置：**
```json
{
  "mcpServers": {
    "warehouse-system": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "-m", "warehouse_mcp"],
      "cwd": "/Users/harvest/project/test_dataset/warehouse_system"
    }
  }
}
```

**现在的配置：**
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

**重要变更：**
1. `cwd` 路径从项目根目录改为 `mcp` 子目录
2. 请将路径改为你的实际项目 mcp 目录路径

## 文档更新

以下文档已更新 MCP 路径引用：

- ✅ `CLAUDE_DESKTOP_CONFIG.md` - 更新配置示例和路径说明
- ✅ `MCP_README.md` - 更新所有路径引用
- ✅ `PROJECT_SUMMARY.md` - 更新项目结构
- ✅ `README.md` - 更新项目结构
- ✅ `start.sh` - 更新启动脚本

## 运行方式

### 1. 启动脚本（推荐）

```bash
./start.sh
```

启动脚本会自动：
- 启动后端服务（端口 2124）
- 启动前端服务（端口 2125）
- 启动 MCP 服务

### 2. 手动启动 MCP

```bash
cd mcp
uv run python warehouse_mcp.py
```

### 3. 使用 MCP Inspector

```bash
cd mcp
npx @modelcontextprotocol/inspector uv run python warehouse_mcp.py
```

## 验证步骤

### 1. 验证文件存在

```bash
ls -la mcp/
```

应该看到：
- `warehouse_mcp.py`
- `mcp_config.json`
- `mcp_pipe.py`

### 2. 验证 MCP 服务器启动

```bash
cd mcp
uv run python warehouse_mcp.py
```

应该看到 FastMCP 的启动信息。

### 3. 更新 Claude Desktop 配置

1. 编辑配置文件
2. 更新 `cwd` 路径为你的 mcp 目录路径
3. 重启 Claude Desktop
4. 测试查询命令

## 获取 mcp 目录路径

```bash
cd /path/to/warehouse_system/mcp
pwd
```

输出示例：
```
/Users/harvest/project/test_dataset/warehouse_system/mcp
```

将此路径复制到 Claude Desktop 配置文件的 `cwd` 字段。

## 测试 MCP 工具

### 在 Claude Desktop 中测试

```
请查询 watcher-xiaozhi(标准版) 的库存
```

### 使用测试脚本

```bash
python3 test/test_mcp.py
```

## 迁移的好处

1. **更清晰的结构** - MCP 相关文件集中管理
2. **更好的可维护性** - 相关代码独立成模块
3. **更容易扩展** - 添加新的 MCP 功能更方便
4. **符合规范** - 遵循项目模块化最佳实践

## 向后兼容

### 需要更新的地方

如果你有自定义脚本或配置引用了旧的 MCP 文件路径，需要更新：

**旧的引用：**
```bash
python3 warehouse_mcp.py
```

**新的引用：**
```bash
python3 mcp/warehouse_mcp.py
```

或者：
```bash
cd mcp
python3 warehouse_mcp.py
```

## 常见问题

### Q: Claude Desktop 无法连接到 MCP 服务器？

A: 检查以下几点：
1. 配置文件的 `cwd` 路径是否指向 mcp 目录
2. 路径是否为绝对路径
3. 是否已重启 Claude Desktop
4. 运行 `cd mcp && uv run python warehouse_mcp.py` 测试是否能正常启动

### Q: MCP 服务器报错找不到 backend 模块？

A: 检查 warehouse_mcp.py 中的路径是否正确：
```python
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(project_root, 'backend')
```

### Q: 需要更新依赖吗？

A: 不需要，依赖没有变化。所有文件只是移动了位置。

## 总结

✅ MCP 文件已成功迁移到 `mcp/` 目录
✅ 所有代码路径引用已更新
✅ 启动脚本已更新
✅ 所有相关文档已更新
✅ MCP 服务器可正常启动和运行

现在 MCP 相关功能组织得更加清晰和专业！
