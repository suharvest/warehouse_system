# Warehouse Management System MCP API Documentation

English | [中文](MCP_README.md)

## Overview

This MCP server provides API interfaces for the warehouse management system, specifically designed for managing watcher-xiaozhi product inventory.

## MCP Tools

### 1. get_today_statistics - Query Today's Statistics

Query today's inbound quantity, outbound quantity, and current total stock.

**Parameters:** None

**Response Example:**
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
  "message": "Query successful: 2024-11-07 inbound 50 items, outbound 30 items, current total stock 3300 items"
}
```

**Field Description:**
- `today_in`: Today's total inbound quantity
- `today_out`: Today's total outbound quantity
- `total_stock`: Current total stock
- `low_stock_count`: Low stock alert count (number of materials below safe stock)
- `net_change`: Today's net change (inbound - outbound)

### 2. query_xiaozhi_stock - Query Stock

Query stock information for a specified watcher-xiaozhi product.

**Parameters:**
- `product_name` (string, optional): Product name, defaults to "watcher-xiaozhi(标准版)"

**Available Product Names:**
- `watcher-xiaozhi(标准版)` (Standard Edition)
- `watcher-xiaozhi(专业版)` (Professional Edition)
- `watcher-xiaozhi整机` (Complete Unit)
- `watcher-xiaozhi主控板` (Main Control Board)
- `watcher-xiaozhi扩展板` (Expansion Board)
- `watcher-xiaozhi外壳(上)` (Upper Enclosure)
- `watcher-xiaozhi外壳(下)` (Lower Enclosure)

**Response Example:**
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
  "message": "Query successful: watcher-xiaozhi(标准版) current stock 52 units"
}
```

### 3. stock_in - Inbound Operation

Add watcher-xiaozhi products to inventory.

**Parameters:**
- `product_name` (string, required): Product name
- `quantity` (integer, required): Inbound quantity (must be greater than 0)
- `reason` (string, optional): Inbound reason, defaults to "采购入库" (Purchase inbound)
- `operator` (string, optional): Operator, defaults to "MCP系统" (MCP System)

**Response Example:**
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
  "message": "Inbound successful: watcher-xiaozhi(标准版) inbound 10 units, stock updated from 52 to 62 units"
}
```

### 4. stock_out - Outbound Operation

Remove watcher-xiaozhi products from inventory.

**Parameters:**
- `product_name` (string, required): Product name
- `quantity` (integer, required): Outbound quantity (must be greater than 0)
- `reason` (string, optional): Outbound reason, defaults to "销售出库" (Sales outbound)
- `operator` (string, optional): Operator, defaults to "MCP系统" (MCP System)

**Response Example (Success):**
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
  "message": "Outbound successful: watcher-xiaozhi(标准版) outbound 5 units, stock updated from 62 to 57 units",
  "warning": ""
}
```

**Response Example (Insufficient Stock):**
```json
{
  "success": false,
  "error": "Insufficient stock",
  "message": "Outbound failed: watcher-xiaozhi(标准版) insufficient stock, current stock 5 units, required outbound 10 units"
}
```

**Response Example (Low Stock Warning):**
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
  "message": "Outbound successful: watcher-xiaozhi(标准版) outbound 5 units, stock updated from 10 to 5 units",
  "warning": "⚠️ Warning: Critical stock level! Current stock 5 units, below 50% of safe stock 15 units"
}
```

### 5. list_xiaozhi_products - List All Products

List stock information for all watcher-xiaozhi related products.

**Parameters:** None

**Response Example:**
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
  "message": "Query successful, found 7 watcher-xiaozhi related products"
}
```

## Configuration

### 1. Configure MCP Service

Copy the configuration template and fill in your settings:

```bash
cd mcp
cp config.yml.example config.yml
```

Edit `config.yml`:

```yaml
# Backend API URL
api_base_url: "http://localhost:2124/api"

# API Key (create in backend admin panel)
api_key: "your-api-key-here"
```

**How to get API Key:**
1. Login to frontend (http://localhost:2125) as admin
2. Go to "User Management" TAB
3. Click "Create Key" in API Key Management section
4. Copy the generated key to `config.yml`

**Environment Variables (optional, higher priority):**
```bash
export WAREHOUSE_API_URL="http://localhost:2124/api"
export WAREHOUSE_API_KEY="your-api-key"
```

### 2. Configure in Claude Desktop

Edit the Claude Desktop configuration file and add the following:

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

**Note:** Please modify the `cwd` path to your actual project's mcp directory path.

### 2. Restart Claude Desktop

After configuration, restart Claude Desktop for changes to take effect.

### 3. Verify MCP Tools

In Claude Desktop, you should see the following tools:
- get_today_statistics
- query_xiaozhi_stock
- stock_in
- stock_out
- list_xiaozhi_products

## Usage Examples

### Example 1: Query Today's Statistics

**Request:**
```
Please query today's warehouse statistics
```

**MCP Call:**
```python
get_today_statistics()
```

**Response:**
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
  "message": "Query successful: 2024-11-07 inbound 50 items, outbound 30 items, current total stock 3300 items"
}
```

### Example 2: Query Standard Edition Stock

**Request:**
```
Please query the stock of watcher-xiaozhi(标准版)
```

**MCP Call:**
```python
query_xiaozhi_stock(product_name="watcher-xiaozhi(标准版)")
```

### Example 3: Inbound 10 Standard Edition Units

**Request:**
```
Please add 10 units of watcher-xiaozhi(标准版) to inventory, reason is new purchase arrival
```

**MCP Call:**
```python
stock_in(
    product_name="watcher-xiaozhi(标准版)",
    quantity=10,
    reason="新采购到货",
    operator="采购部"
)
```

### Example 4: Outbound 5 Standard Edition Units

**Request:**
```
watcher-xiaozhi(标准版) sales outbound 5 units
```

**MCP Call:**
```python
stock_out(
    product_name="watcher-xiaozhi(标准版)",
    quantity=5,
    reason="销售出库",
    operator="销售部"
)
```

### Example 5: List All Products

**Request:**
```
List all watcher-xiaozhi products
```

**MCP Call:**
```python
list_xiaozhi_products()
```

## Frontend Real-time Updates

After MCP operations complete, the frontend interface (http://localhost:2125) will automatically update within 3 seconds to display the latest stock data.

### Multi-language Support

The frontend interface supports Chinese/English switching:
- Click the language dropdown in the top-right corner
- Select "中文简体" or "English"
- Page content switches instantly

### Update Mechanism

1. MCP tools call backend service via HTTP API
2. Backend service modifies the database
3. Frontend automatically refreshes stock list every 3 seconds
4. No manual page refresh needed to see changes

**Note**: Ensure the backend service (port 2124) is running before using MCP service.

### Verify Updates

Follow these steps to verify:

1. **Open Frontend Page**
   ```
   http://localhost:2125
   ```

2. **Enter "标准版" in the search box** to view current stock

3. **Call MCP through Claude Desktop**
   ```
   Please add 5 units of watcher-xiaozhi(标准版) to inventory
   ```

4. **Observe Frontend Page**
   - Wait up to 3 seconds
   - Stock quantity will update automatically
   - If below safe stock, status label will change

## Error Handling

### Common Errors

1. **Product Not Found**
   ```json
   {
     "success": false,
     "error": "Product not found: xxx",
     "message": "Product 'xxx' does not exist, please check the product name"
   }
   ```

2. **Insufficient Stock**
   ```json
   {
     "success": false,
     "error": "Insufficient stock",
     "message": "Outbound failed: insufficient stock..."
   }
   ```

3. **Invalid Quantity**
   ```json
   {
     "success": false,
     "error": "Inbound quantity must be greater than 0",
     "message": "Inbound failed: quantity -5 is invalid"
   }
   ```

## Starting MCP Service

### Using Standalone Startup Scripts

MCP service has been separated into standalone scripts. Configure `MCP_ENDPOINT` in the script before starting.

**macOS/Linux:**
```bash
cd mcp
# Edit start_mcp.sh to set MCP_ENDPOINT
./start_mcp.sh
```

**Windows (PowerShell):**
```powershell
cd mcp
# Edit start_mcp.ps1 to set MCP_ENDPOINT
.\start_mcp.ps1
```

### Configuration

Find and modify the following configuration in the startup script:

```bash
# macOS/Linux (start_mcp.sh)
export MCP_ENDPOINT="ws://localhost:8080/mcp"

# Windows (start_mcp.ps1)
$env:MCP_ENDPOINT = "ws://localhost:8080/mcp"
```

## Testing MCP Server

### Method 1: Use Test Script

```bash
# Run from project root directory
python3 test/test_mcp.py

# Or run from test directory
cd test
python3 test_mcp.py
```

### Method 2: Use MCP Inspector

```bash
cd mcp
npx @modelcontextprotocol/inspector uv run python warehouse_mcp.py
```

### Method 3: Run Directly

```bash
cd mcp
uv run python warehouse_mcp.py
```

## Logging

The MCP server logs all operations, including:
- Query operations
- Inbound operations (product name, quantity, operator)
- Outbound operations (product name, quantity, operator, remaining stock)
- Error messages

## Security Notes

1. MCP tools operate on database via API, use with caution
2. Recommend regular backups of database file `backend/warehouse.db`
3. Outbound operations automatically check if stock is sufficient
4. Inbound/outbound quantities must be greater than 0
5. Ensure backend service is running before using MCP

## Technical Details

- **MCP Framework**: FastMCP
- **Transport**: stdio
- **Backend Framework**: FastAPI
- **Database**: SQLite
- **Python Version**: 3.12+
- **Dependencies**: fastmcp, mcp, requests
- **Architecture**: MCP → HTTP API → Database (Single data access layer)

## Troubleshooting

### MCP Tools Not Working

1. Check if Claude Desktop configuration is correct
2. Confirm project path is correct
3. Restart Claude Desktop
4. Check if `uv` and related dependencies are installed

### Data Not Syncing

1. Confirm backend service is running (port 2124)
2. Check if frontend page is refreshing normally (every 3 seconds)
3. Verify database file exists

### Operation Failed

1. Confirm backend service is running (port 2124)
2. Check if product name is correct (case-sensitive)
3. Confirm quantity is greater than 0
4. Check if stock is sufficient for outbound operations
5. Review MCP error messages

## Contact & Support

For questions, please refer to:
- `README.md` - Project documentation (Chinese)
- `README_EN.md` - Project documentation (English)
- `USAGE.md` - Usage documentation
- `UPDATE_LOG.md` - Update log
