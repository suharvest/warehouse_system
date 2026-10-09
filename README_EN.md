# Warehouse Management System

English | [中文](README.md)

> [!TIP]
> **Looking for Industrial Turnkey Hardware & Edge Gateways?**  
> This warehouse management system and MCP voice integration are verified on Seeed Studio industrial edge controllers.  
> 🛒 **[Get reComputer Industrial Edge Gateway](https://www.seeedstudio.com/reComputer-R1000-Edge-Controller-powered-by-Raspberry-Pi-CM4-p-5900.html?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hero_banner)** | 📖 **[Explore Smart Warehouse Solution & BOM](https://www.seeed.cc/solutions/smart-warehouse-management?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hero_banner)**

A warehouse management system based on FastAPI + SQLite, with voice control support (MCP).

## Demo Video

[Watch Demo Video](assets/demo_video.mp4)

## Features

- 📊 Inventory Management: Stock in/out, batch tracking, low stock alerts
- 📈 Data Analytics: Trend charts, category stats, Top 10 rankings
- 🔐 Access Control: User management, API keys, three-level permissions
- 👥 Contact Management: Supplier/customer linked to inventory records
- 🌐 Multi-language: Chinese/English switching
- 🗣️ Voice Control: Voice operations via MCP

## Quick Start

### Quick Deploy (Pre-built Image)

```bash
docker run -d -p 1025:1025 \
  -v warehouse_data:/app/data \
  sensecraft-missionpack.seeed.cn/solution/warehouse:latest
```

Visit http://localhost:1025. First visit requires registering an admin account.

### Docker Deployment (Build from Source)

```bash
git clone https://github.com/suharvest/warehouse_system.git
cd warehouse_system
docker-compose -f docker-compose.prod.yml up -d
```

**Common Commands:**
```bash
docker-compose -f docker-compose.prod.yml logs -f     # View logs
docker-compose -f docker-compose.prod.yml down        # Stop services
docker-compose -f docker-compose.prod.yml up -d --build  # Rebuild
```

### Local Development

Requires [uv](https://docs.astral.sh/uv/) (Python package manager).

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
irm https://astral.sh/uv/install.ps1 | iex
```

**Start Services:**
```bash
./start.sh --vite   # macOS/Linux (dev mode, recommended)
.\start.ps1 -Vite   # Windows (dev mode, recommended)
```

> Production mode requires building frontend first: `cd frontend && npm install && npm run build`

### MCP Voice Control (Optional)

1. Login and create an API key in "User Management" → "API Keys"
2. Configure MCP:
   ```bash
   cd mcp
   cp config.yml.example config.yml
   # Edit config.yml with your API key
   ```
3. Start:
   ```bash
   export MCP_ENDPOINT="wss://your-endpoint"  # or Windows: $env:MCP_ENDPOINT="..."
   ./start_mcp.sh  # or Windows: .\start_mcp.ps1
   ```

## Verified Hardware & Deployment Platforms

This WMS and MCP voice hub are tested for 24/7 industrial deployment on official Seeed Studio hardware:

| Device Model | Compute / Form Factor | Industrial Features | Deployment Role | Buy Sample / Kit |
|---|---|---|---|---|
| **reComputer R1000** | Raspberry Pi CM4 Industrial Controller | DIN-rail, Isolated RS485, Dual GbE, UPS Slot | Primary local WMS server & MCP voice gateway | [🛒 Buy Now](https://www.seeedstudio.com/reComputer-R1000-Edge-Controller-powered-by-Raspberry-Pi-CM4-p-5900.html?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hw_table) |
| **reComputer Industrial J4012** | NVIDIA Jetson Orin NX 16GB | 100 TOPS, Fanless -20~60°C, Isolated DI/DO | Vision-based pallet inspection & multi-stream OCR | [🛒 Buy Now](https://www.seeedstudio.com/reComputer-Industrial-J4012-p-5586.html?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hw_table) |
| **SenseCAP T1000-A** | LoRaWAN / BLE Asset Tracker | IP65, Light/Temp/Acc Sensor, SOS Button | Forklift & high-value pallet location tracking | [🛒 Buy Now](https://www.seeedstudio.com/SenseCAP-Card-Tracker-T1000-A-p-5696.html?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=hw_table) |

## Documentation

- [MCP Integration Overview](mcp/README_EN.md) — **entry point for both integration paths**: wrap your own system as MCP, or bridge a third-party system in
- [MCP Integration Guide](docs/MCP_External_System_Integration.md)
- [WMS Provider Development Guide](docs/WMS_Provider_Development.md) — Integrate with third-party WMS
- [System User Guide](docs/Warehouse_System_Guide.md)
- [Changelog](CHANGELOG_EN.md)

## API Documentation

After starting, visit http://localhost:2124/docs for complete API documentation.

## 💼 Enterprise & Commercial Deployment

Deploying smart warehouse tracking or voice-driven picking across enterprise logistics hubs?

- **Volume Hardware Pricing**: Tiered pricing available for regional distribution centers and 100+ warehouse deployments.
- **Custom Industrial Gateways**: Custom DIN-rail carriers, pre-loaded golden OS images, dual 4G/5G failover, and international certifications (CE/FCC/RoHS).
- **ERP / WMS System Integration**: Seamless integration with SAP, Kingdee, Yonyou, and custom ERPs via standard MCP & REST APIs.

👉 **[Contact Seeed Enterprise Solution Team](https://www.seeed.cc/customization?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=footer_enterprise)** or submit your project requirements at the [Smart Warehouse Solution Page](https://www.seeed.cc/solutions/smart-warehouse-management?utm_source=github&utm_medium=readme&utm_campaign=warehouse_system&utm_content=footer_solution).

## License

MIT License
