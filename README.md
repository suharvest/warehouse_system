# 仓库管理系统

[English](README_EN.md) | 中文

> [!TIP]
> **寻找即插即用的工业级边缘网关与仓储硬件套件？**  
> 本系统与 MCP 语音交互已在 Seeed Studio 工业边缘控制器与网关设备上完成实机验证与调优。  
> 🛒 **[获取 reComputer 工业边缘控制器](https://www.seeedstudio.com/reComputer-R1000-Edge-Controller-powered-by-Raspberry-Pi-CM4-p-5900.html?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hero_banner)** | 📖 **[探索智能仓储解决方案与 BOM 配置器](https://www.seeed.cc/solutions/smart-warehouse-management?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hero_banner)**

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

### 一键部署（预构建镜像）

```bash
docker run -d -p 1025:1025 \
  -v warehouse_data:/app/data \
  sensecraft-missionpack.seeed.cn/solution/warehouse:latest
```

访问 http://localhost:1025，首次需注册管理员账户。

### Docker 部署（从源码构建）

```bash
git clone https://github.com/suharvest/warehouse_system.git
cd warehouse_system
docker-compose -f docker-compose.prod.yml up -d
```

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

## 推荐运行硬件 (Verified Hardware)

本系统与 MCP 语音服务支持跨平台部署，已在以下 Seeed Studio 工业级边缘网关上完成 7×24 小时长期稳定性验证：

| 推荐设备型号 | 计算与核心规格 | 工业接口与特性 | 典型部署角色 | 采购样机 / 套件 |
|---|---|---|---|---|
| **reComputer R1000** | Raspberry Pi CM4, 4-Core 1.5GHz | 导轨式安装, 双千兆网口, 隔离 RS485, UPS 扩展 | 本地 WMS 主机与 MCP 语音网关一体机 | [🛒 立即选购](https://www.seeedstudio.com/reComputer-R1000-Edge-Controller-powered-by-Raspberry-Pi-CM4-p-5900.html?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hw_table) |
| **reComputer Industrial J4012** | NVIDIA Jetson Orin NX 16GB, 100 TOPS | IP40 无风扇, -20~60°C 宽温, 光耦隔离 DI/DO | 托盘垛位视觉盘点、条码 OCR 与多路视频聚合 | [🛒 立即选购](https://www.seeedstudio.com/reComputer-Industrial-J4012-p-5586.html?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hw_table) |
| **SenseCAP T1000-A** | LoRaWAN / BLE 高精度人员资产工牌 | IP65 防护, 温湿度/光照/加速度传感, SOS 按键 | 叉车室内外轨迹追踪与贵重货物垛位定位 | [🛒 立即选购](https://www.seeedstudio.com/SenseCAP-Card-Tracker-T1000-A-p-5696.html?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hw_table) |

## 文档

- [MCP 集成总览](mcp/README.md) — **两条集成路径的入口**：把自己的系统封装成 MCP，或把第三方系统桥接进来
- [MCP 集成指南](docs/MCP_External_System_Integration.md)
- [WMS Provider 开发指南](docs/WMS_Provider_Development.md) — 对接第三方 WMS 系统
- [系统使用指南](docs/Warehouse_System_Guide.md)
- [更新记录](CHANGELOG.md)

## API 文档

启动后访问 http://localhost:2124/docs 查看完整 API 文档。

## 💼 企业级方案与商业定制 (Enterprise & Commercial Deployment)

如需在多区域物流中心、大型工厂仓库批量部署或对接现有企业系统：

- **硬件批量集采**：支持 100+ 台规模化部署的阶梯采购价格与供货保障。
- **工业网关 ODM/OEM 定制**：提供专属外壳丝印、载板接口客制化、预装出厂 Golden 系统镜像及 CE/FCC 全球合规认证。
- **现有 ERP / WMS 快速桥接**：通过标准化 MCP 协议与 REST API 快速对接 SAP、金蝶、用友或自研 WMS。

👉 **[联系 Seeed 企业解决方案团队](https://www.seeed.cc/customization?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=footer_enterprise)** 或访问 [智能仓储数字化方案页](https://www.seeed.cc/solutions/smart-warehouse-management?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=footer_solution) 提交项目需求。

## 许可证

MIT License
