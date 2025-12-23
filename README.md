# 仓库管理系统

[English](README_EN.md) | 中文

基于 FastAPI + SQLite 的仓库管理系统，支持语音控制（MCP）。

## 演示视频

[点击观看演示视频](assets/demo_video.mp4)

## 功能特性

- 📊 库存管理：出入库、批次追踪、库存预警
- 📈 数据分析：趋势图表、分类统计、TOP10 排行
- 🔐 权限控制：用户管理、API 密钥、三级权限
- 👥 联系方管理：供应商/客户关联出入库记录
- 🌐 多语言：中英文切换
- 🗣️ 语音控制：通过 MCP 实现语音操作

## 快速开始

### Docker 部署（推荐）

```bash
git clone https://github.com/suharvest/warehouse_system.git
cd warehouse_system
docker-compose -f docker-compose.prod.yml up -d
```

访问 http://localhost:2125，首次需注册管理员账户。

**常用命令：**
```bash
docker-compose -f docker-compose.prod.yml logs -f     # 查看日志
docker-compose -f docker-compose.prod.yml down        # 停止服务
docker-compose -f docker-compose.prod.yml up -d --build  # 重新构建
```

### 本地开发

需要安装 [uv](https://docs.astral.sh/uv/)（Python 包管理）。

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
irm https://astral.sh/uv/install.ps1 | iex
```

**启动服务：**
```bash
./start.sh --vite   # macOS/Linux（开发模式，推荐）
.\start.ps1 -Vite   # Windows（开发模式，推荐）
```

> 生产模式需要先构建前端：`cd frontend && npm install && npm run build`

### MCP 语音控制（可选）

1. 登录系统，在「用户管理」→「API 密钥」创建密钥
2. 配置 MCP：
   ```bash
   cd mcp
   cp config.yml.example config.yml
   # 编辑 config.yml 填入 API 密钥
   ```
3. 启动：
   ```bash
   export MCP_ENDPOINT="wss://your-endpoint"  # 或 Windows: $env:MCP_ENDPOINT="..."
   ./start_mcp.sh  # 或 Windows: .\start_mcp.ps1
   ```

## 文档

- [MCP 集成指南](docs/MCP_External_System_Integration.md)
- [系统使用指南](docs/Warehouse_System_Guide.md)
- [更新记录](CHANGELOG.md)

## API 文档

启动后访问 http://localhost:2124/docs 查看完整 API 文档。

## 许可证

MIT License
