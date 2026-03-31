# Warehouse Management System

English | [中文](README.md)

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

## Documentation

- [MCP Integration Guide](docs/MCP_External_System_Integration.md)
- [WMS Provider Development Guide](docs/WMS_Provider_Development.md) — Integrate with third-party WMS
- [System User Guide](docs/Warehouse_System_Guide.md)
- [Changelog](CHANGELOG_EN.md)

## API Documentation

After starting, visit http://localhost:2124/docs for complete API documentation.

## License

MIT License
