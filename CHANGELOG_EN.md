# Changelog

## 2025-12-09 v2.1.0

### UI/UX Improvements
- **Visual Refresh**: New warm theme (`#f4f7f0`) and rounded card design
- **Layout Optimization**: Pagination integrated into table cards for a compact look
- **Validation**: Added red star markers for required fields and robust submission validation

### Feature Enhancements
- **Export Filters**: Excel export now supports filtering by Time, Name, Category, and Status
- **Excel Import**:
  - Added "Disable missing SKUs" option
  - Automatically updates material attributes (Safe Stock, Unit, etc.)
  - Import difference preview and confirmation
- **Back Navigation**: Added "Back to List" button on details page, preserving filter state

## v2.0.0

### Architecture Upgrade
- **Backend migrated to FastAPI**: Migrated from Flask to FastAPI framework
  - Auto-generated API documentation (Swagger UI: `/docs`)
  - Pydantic response models with type validation
  - Modern async architecture support

### New APIs
- **Stock In**: `POST /api/materials/stock-in`
- **Stock Out**: `POST /api/materials/stock-out`

### MCP Architecture Optimization
- MCP service now calls backend via HTTP API instead of direct database operations
- Single data access layer for easier maintenance and extensibility

## v1.1.0

### Features
- **Multi-language Support**: Chinese/English switching
- Fixed spacing issue between inventory list and Top 10 chart
