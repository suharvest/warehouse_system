# Warehouse Management System

English | [中文](README.md)

A smart hardware warehouse management dashboard based on Python FastAPI + SQLite.

## Features

- 📊 **Real-time Statistics**: Total stock, daily in/out, low stock alerts
- 📈 **Trend Analysis**: 7-day in/out trend visualization
- 🥧 **Category Distribution**: Stock distribution pie chart
- 📋 **Top 10 Display**: Top materials by stock quantity
- ⚠️ **Alert List**: Low stock material warnings
- 🌐 **Multi-language Support**: Chinese/English switching
- 📱 **Responsive Design**: Adapts to different screen sizes
- 🔐 **User Permission Management**: Three-level access control (view/operate/admin)
- 🔑 **API Key Management**: Multiple keys with independent permissions for MCP terminal access
- 👥 **Contact Management**: Supplier/Customer management linked to inventory records
- 📦 **Batch Management**: Auto batch number generation with FIFO stock-out algorithm
- 💾 **Database Management**: Export, import, and clear warehouse data (user data unaffected)

## Demo Video

[Watch Demo Video](assets/demo_video.mp4)


## Changelog

[View Full Changelog](CHANGELOG_EN.md)


## Tech Stack

### Backend
- Python 3.12
- FastAPI (Web Framework)
- Uvicorn (ASGI Server)
- Pydantic (Data Validation)
- SQLite (Database)
- uv (Package Manager)

### Frontend
- Native HTML/CSS/JavaScript
- ECharts (Charting Library)
- i18n.js (Internationalization)
- Responsive Design

## Quick Start

### 1. One-click Start

**macOS/Linux:**
```bash
./start.sh
```

**Windows (PowerShell):**
```powershell
.\start.ps1
```

After starting, visit:
- Frontend: http://localhost:2125
- API Docs: http://localhost:2124/docs

### 2. Start MCP Service (Optional)

MCP service has been separated into standalone scripts. Configure `MCP_ENDPOINT` environment variable before starting.

**macOS/Linux:**
```bash
cd mcp
# Edit start_mcp.sh to configure MCP_ENDPOINT
./start_mcp.sh
```

**Windows (PowerShell):**
```powershell
cd mcp
# Edit start_mcp.ps1 to configure MCP_ENDPOINT
.\start_mcp.ps1
```

### 3. Manual Start

#### Initialize Database
```bash
cd backend
uv run python database.py
```

#### Start Backend Service (Port 2124)
```bash
uv run python run_backend.py
```

#### Start Frontend Service (Port 2125)
```bash
cd frontend
python3 server.py
```

## Project Structure

```
warehouse_system/
├── backend/              # Backend code
│   ├── app.py           # FastAPI main application
│   ├── models.py        # Pydantic response models
│   ├── database.py      # Database initialization and data generation
│   ├── Dockerfile       # Backend Docker image config
│   └── warehouse.db     # SQLite database file (generated after running)
├── frontend/            # Frontend code
│   ├── index.html       # Main page
│   ├── style.css        # Stylesheet
│   ├── app.js           # Main page JavaScript logic
│   ├── i18n.js          # Internationalization config
│   ├── server.py        # Static file server
│   └── Dockerfile       # Frontend Docker image config
├── mcp/                 # MCP service
│   ├── warehouse_mcp.py # MCP server
│   ├── config.yml       # MCP config (API URL, key)
│   ├── config.yml.example # Config template
│   ├── start_mcp.sh     # Startup script (macOS/Linux)
│   ├── start_mcp.ps1    # Startup script (Windows)
│   ├── MCP_README.md    # MCP documentation (Chinese)
│   └── MCP_README_EN.md # MCP documentation (English)
├── test/                # Test files
│   ├── backend/         # Backend feature tests
│   ├── data/            # Test data
│   ├── test_mcp.py      # MCP tests
│   ├── test_api.py      # API tests
│   ├── run_all_tests.sh # Test script
│   └── README.md        # Test documentation
├── docs/                # Project documentation
│   ├── CLAUDE_DESKTOP_CONFIG.md  # Claude Desktop config guide
│   ├── TESTING_GUIDE.md          # Testing guide
│   ├── Warehouse_System_Guide.md # System usage guide
│   └── assets/                   # Documentation images
├── docker-compose.yml   # Docker Compose configuration
├── start.sh             # Startup script (macOS/Linux)
├── start.ps1            # Startup script (Windows)
├── CHANGELOG.md         # Changelog (Chinese)
├── CHANGELOG_EN.md      # Changelog (English)
├── README.md            # Project documentation (Chinese)
└── README_EN.md         # Project documentation (English)
```

## Multi-language Support

The system supports Chinese/English switching:

1. Click the language dropdown in the top-right corner
2. Select "中文简体" or "English"
3. Page content switches instantly without refresh

Translated content includes:
- Page titles and subtitles
- Statistics card labels
- Chart titles and legends
- Table headers
- Status text (Normal/Low/Critical)
- Search box placeholder

## Data Description

### Material Categories
- **Mainboard**: watcher-xiaozhi main board, expansion board, power management board, etc.
- **Sensors**: Camera, microphone, PIR sensor, temperature/humidity sensor, etc.
- **Enclosure Parts**: Enclosure, bracket, screws, etc.
- **Cables**: USB cable, power cable, ribbon cable, etc.
- **Packaging**: Packaging box, manual, warranty card, etc.
- **Power**: Power adapter, lithium battery, etc.
- **Auxiliary Materials**: Thermal silicone, insulation tape, etc.
- **Finished Products**: watcher-xiaozhi complete units and variants

### Initial Data
- Material types: 37
- Total stock: ~3000+ items
- History records: ~100+ in/out records in the past 7 days
- watcher-xiaozhi related stock: ~80-100 finished units + components

## API Endpoints

### Get Dashboard Statistics
```
GET /api/dashboard/stats
```

### Get Category Distribution
```
GET /api/dashboard/category-distribution
```

### Get 7-day Trend
```
GET /api/dashboard/weekly-trend
```

### Get Top 10 Stock
```
GET /api/dashboard/top-stock
```

### Get Low Stock Alerts
```
GET /api/dashboard/low-stock-alert
```

### Get All Materials
```
GET /api/materials/all
```

### Get Product Statistics
```
GET /api/materials/product-stats?name=product_name
```

### Get Product Trend
```
GET /api/materials/product-trend?name=product_name
```

### Get Product In/Out Records
```
GET /api/materials/product-records?name=product_name
```

### Get watcher-xiaozhi Related Stock
```
GET /api/materials/xiaozhi
```

### Stock In Operation
```
POST /api/materials/stock-in
Content-Type: application/json

{
  "product_name": "product_name",
  "quantity": 10,
  "reason": "inbound_reason",
  "operator": "operator_name"
}
```

### Stock Out Operation
```
POST /api/materials/stock-out
Content-Type: application/json

{
  "product_name": "product_name",
  "quantity": 5,
  "reason": "outbound_reason",
  "operator": "operator_name"
}
```

### Database Management (Admin Only)

#### Export Warehouse Data
```
GET /api/database/export
```
Downloads a SQLite file containing warehouse data (excludes user accounts and API keys).

#### Import Warehouse Data
```
POST /api/database/import
Content-Type: multipart/form-data

file: <.db file>
```
Restores warehouse data from a backup file. This will clear existing warehouse data.

#### Clear Warehouse Data
```
POST /api/database/clear
Content-Type: application/json

{
  "confirm": true
}
```
Clears all warehouse data (materials, records, batches, contacts). User accounts and API keys are not affected.

## Stop Services

If started with `start.sh`, press `Ctrl+C` to stop all services.

If started manually, terminate backend and frontend processes separately.

## Testing

### Run All Tests
```bash
./test/run_all_tests.sh
```

### Individual Tests
```bash
# MCP tool tests
python3 test/test_mcp.py

# API endpoint tests
python3 test/test_api.py
```

See `test/README.md` for details.

## Notes

1. Ensure ports 2124 and 2125 are not in use
2. First run will automatically create database and initial data
3. Database file is located at `backend/warehouse.db`
4. To regenerate data, delete the database file and run again

## Development

### Reset Database
```bash
rm backend/warehouse.db
cd backend
uv run python database.py
```

### Add Dependencies
```bash
uv add <package_name>
```

### Add New Language
Edit `frontend/i18n.js` and add new language translations to the `translations` object.

## License

MIT License
