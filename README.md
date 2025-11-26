# 仓库管理系统

[English](README_EN.md) | 中文

一个基于 Python FastAPI + SQLite 的智能硬件仓库管理系统仪表盘。

## 功能特性

- 📊 **实时统计**：库存总量、今日出入库、库存预警
- 📈 **趋势分析**：近7天出入库趋势可视化
- 🥧 **分类分布**：库存类型占比饼图
- 📋 **TOP10展示**：库存最多的物料排行
- ⚠️ **预警列表**：低于安全库存的物料提醒
- 🌐 **多语言支持**：支持中英文切换
- 📱 **响应式设计**：适配不同屏幕尺寸

## 最新更新 (v2.0.0)

### 架构升级
- **后端迁移至 FastAPI**：从 Flask 迁移到 FastAPI 框架
  - 自动生成 API 文档（Swagger UI: `/docs`）
  - Pydantic 响应模型，提供类型验证
  - 更现代的异步架构支持

### 新增 API
- **入库接口**: `POST /api/materials/stock-in`
- **出库接口**: `POST /api/materials/stock-out`

### MCP 架构优化
- MCP 服务改为通过 HTTP API 调用后端，而非直接操作数据库
- 单一数据访问层，便于维护和扩展

### v1.1.0 功能
- **多语言支持**：中英文切换功能
- 修复库存列表与 TOP10 图表间距问题

## 技术栈

### 后端
- Python 3.12
- FastAPI (Web框架)
- Uvicorn (ASGI服务器)
- Pydantic (数据验证)
- SQLite (数据库)
- uv (包管理工具)

### 前端
- 原生 HTML/CSS/JavaScript
- ECharts (图表库)
- i18n.js (国际化)
- 响应式设计

## 快速开始

### 1. 一键启动

**macOS/Linux:**
```bash
./start.sh
```

**Windows (PowerShell):**
```powershell
.\start.ps1
```

启动后访问：
- 前端页面：http://localhost:2125
- API 文档：http://localhost:2124/docs

### 2. 启动 MCP 服务（可选）

MCP 服务已独立为单独的启动脚本，需要配置 `MCP_ENDPOINT` 环境变量。

**macOS/Linux:**
```bash
cd mcp
# 编辑 start_mcp.sh 配置 MCP_ENDPOINT
./start_mcp.sh
```

**Windows (PowerShell):**
```powershell
cd mcp
# 编辑 start_mcp.ps1 配置 MCP_ENDPOINT
.\start_mcp.ps1
```

### 3. 手动启动

#### 初始化数据库
```bash
cd backend
uv run python database.py
```

#### 启动后端服务（端口 2124）
```bash
uv run python run_backend.py
```

#### 启动前端服务（端口 2125）
```bash
cd frontend
python3 server.py
```

## 项目结构

```
warehouse_system/
├── backend/              # 后端代码
│   ├── app.py           # FastAPI 应用主文件
│   ├── models.py        # Pydantic 响应模型
│   ├── database.py      # 数据库初始化和数据生成
│   └── warehouse.db     # SQLite 数据库文件（运行后生成）
├── frontend/            # 前端代码
│   ├── index.html       # 主页面
│   ├── product_detail.html  # 产品详情页
│   ├── style.css        # 样式文件
│   ├── app.js           # 主页 JavaScript 逻辑
│   ├── product_detail.js    # 详情页 JavaScript 逻辑
│   ├── i18n.js          # 国际化配置
│   └── server.py        # 静态文件服务器
├── mcp/                 # MCP 服务
│   ├── warehouse_mcp.py # MCP 服务器
│   ├── mcp_config.json  # MCP 配置
│   ├── mcp_pipe.py      # MCP 管道
│   ├── start_mcp.sh     # MCP 启动脚本 (macOS/Linux)
│   ├── start_mcp.ps1    # MCP 启动脚本 (Windows)
│   └── MCP_README.md    # MCP 文档
├── test/                # 测试文件
│   ├── test_mcp.py      # MCP 测试
│   ├── test_api.py      # API 测试
│   ├── run_all_tests.sh # 测试脚本
│   └── README.md        # 测试文档
├── start.sh             # 启动脚本 (macOS/Linux)
├── start.ps1            # 启动脚本 (Windows)
├── README.md            # 项目说明（中文）
└── README_EN.md         # 项目说明（英文）
```

## 多语言支持

系统支持中英文切换：

1. 点击右上角的语言下拉菜单
2. 选择 "中文简体" 或 "English"
3. 页面内容即时切换，无需刷新

支持翻译的内容：
- 页面标题和副标题
- 统计卡片标签
- 图表标题和图例
- 表格表头
- 状态文本（正常/偏低/告急）
- 搜索框占位符

## 数据说明

### 物料分类
- **主板类**：watcher-xiaozhi主控板、扩展板、电源管理板等
- **传感器类**：摄像头、麦克风、PIR传感器、温湿度传感器等
- **外壳配件类**：外壳、支架、螺丝等
- **线材类**：USB线、电源线、排线等
- **包装类**：包装盒、说明书、保修卡等
- **电源类**：电源适配器、锂电池等
- **辅料类**：导热硅胶、绝缘胶带等
- **成品**：watcher-xiaozhi整机及各版本

### 初始数据量
- 物料种类：37种
- 总库存量：约3000+件
- 历史记录：近7天约100+条出入库记录
- watcher-xiaozhi相关库存：约80-100台成品 + 配套零部件

## API 接口

### 获取仪表盘统计
```
GET /api/dashboard/stats
```

### 获取类型分布
```
GET /api/dashboard/category-distribution
```

### 获取近7天趋势
```
GET /api/dashboard/weekly-trend
```

### 获取库存TOP10
```
GET /api/dashboard/top-stock
```

### 获取库存预警
```
GET /api/dashboard/low-stock-alert
```

### 获取所有物料
```
GET /api/materials/all
```

### 获取产品统计
```
GET /api/materials/product-stats?name=产品名称
```

### 获取产品趋势
```
GET /api/materials/product-trend?name=产品名称
```

### 获取产品出入库记录
```
GET /api/materials/product-records?name=产品名称
```

### 获取watcher-xiaozhi相关库存
```
GET /api/materials/xiaozhi
```

### 入库操作
```
POST /api/materials/stock-in
Content-Type: application/json

{
  "product_name": "产品名称",
  "quantity": 10,
  "reason": "入库原因",
  "operator": "操作人"
}
```

### 出库操作
```
POST /api/materials/stock-out
Content-Type: application/json

{
  "product_name": "产品名称",
  "quantity": 5,
  "reason": "出库原因",
  "operator": "操作人"
}
```

## 停止服务

如果使用 `start.sh` 启动，按 `Ctrl+C` 即可停止所有服务。

如果手动启动，需要分别终止后端和前端进程。

## 测试

### 运行所有测试
```bash
./test/run_all_tests.sh
```

### 单独测试
```bash
# MCP 工具测试
python3 test/test_mcp.py

# API 接口测试
python3 test/test_api.py
```

详见 `test/README.md`

## 注意事项

1. 确保端口 2124 和 2125 未被占用
2. 首次运行会自动创建数据库和初始数据
3. 数据库文件位于 `backend/warehouse.db`
4. 重新生成数据可删除数据库文件后重新运行

## 开发说明

### 重置数据库
```bash
rm backend/warehouse.db
cd backend
uv run python database.py
```

### 添加依赖
```bash
uv add <package_name>
```

### 添加新语言
编辑 `frontend/i18n.js`，在 `translations` 对象中添加新语言的翻译。

## 许可证

MIT License
