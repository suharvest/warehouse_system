# Changelog

## 2025-12-20 v3.0.0

This major upgrade adds three core functional modules to the warehouse management system: User Permission Management, Contact Management, and Batch Management.

### User Management & Permission Control

**New Database Tables**
- `users`: User accounts (username, password hash, role, display name)
- `sessions`: Session management (token, expiration time)
- `api_keys`: API keys (for MCP terminal identity verification)

**Permission Levels**
| Role | Permissions |
|------|-------------|
| `view` | Read-only access to all data |
| `operate` | view + Stock In/Out/Import/Export/Manage Contacts |
| `admin` | operate + User Management/API Key Management |
| Guest | Same as view, no login required |

**New APIs**
- Authentication: `GET /api/auth/status`, `POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
- User Management: `GET/POST/PUT/DELETE /api/users` (admin only)
- API Keys: `GET/POST/DELETE /api/api-keys` (admin only)

**Frontend Changes**
- Login/Register modal
- First-time admin setup flow
- User Management TAB (admin only)
- API Key management interface
- `data-min-role` attribute for button visibility control
- Current user status display in header

### Contact Management

**New Database Tables**
- `contacts`: Contact (Supplier/Customer) information

**Features**
- Contacts TAB (5th tab)
- Contact CRUD modal
- Select contact during stock in/out (Stock In → Supplier, Stock Out → Customer)
- Contact displayed in inventory records table
- Excel export includes "Contact" column

**New APIs**
- `GET /api/contacts`: Contact list (pagination + filtering)
- `GET /api/contacts/suppliers`: Supplier dropdown list
- `GET /api/contacts/customers`: Customer dropdown list
- `POST/PUT/DELETE /api/contacts`: Contact CRUD (operate permission)

### Batch Management

**New Database Tables**
- `batches`: Batch records (batch number, remaining quantity, initial quantity, supplier)
- `batch_consumptions`: Batch consumption records (FIFO consumption details during stock out)

**Batch Number Format**
- Format: `YYYYMMDD-XXX` (e.g., 20251220-001)
- Starts from 001 each day, independent count per material

**FIFO Stock Out Algorithm**
1. Get non-exhausted batches for the material, sorted by `created_at` ASC
2. Consume from oldest batch first until quantity is satisfied
3. Update batch remaining quantity, record consumption details
4. Mark batch as `is_exhausted = 1` when depleted

**API Response Changes**
| API | New Response Fields |
|-----|---------------------|
| `stock_in` | `batch: {batch_no, batch_id, quantity}` |
| `stock_out` | `batch_consumptions: [{batch_no, batch_id, quantity, remaining}]` |
| `inventory/records` | `batch_id, batch_no, batch_details` |

**Frontend Changes**
- "Batch" column added to inventory records table
- Stock in records display batch number
- Stock out records display batch consumption details (e.g., `20251220-001×30, 20251220-002×20`)
- Excel export includes batch information

### Database Schema

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│   users     │     │ inventory_records│     │  contacts   │
├─────────────┤     ├──────────────────┤     ├─────────────┤
│ id          │     │ id               │     │ id          │
│ username    │     │ material_id      │     │ name        │
│ password    │     │ type             │  ┌──│ is_supplier │
│ role        │     │ quantity         │  │  │ is_customer │
│ display_name│     │ operator         │  │  │ is_disabled │
│ is_disabled │     │ reason           │  │  └─────────────┘
│ created_at  │     │ contact_id    ───┘
└─────────────┘     │ batch_id      ───┐
                    │ created_at       │
┌─────────────┐     └──────────────────┘
│  sessions   │              │
├─────────────┤              │
│ id          │     ┌────────┴─────────┐
│ user_id     │     ▼                  ▼
│ token       │  ┌─────────┐    ┌──────────────────┐
│ expires_at  │  │ batches │    │batch_consumptions│
└─────────────┘  ├─────────┤    ├──────────────────┤
                 │ id      │◄───│ batch_id         │
┌─────────────┐  │ batch_no│    │ record_id        │
│  api_keys   │  │material │    │ quantity         │
├─────────────┤  │quantity │    │ created_at       │
│ id          │  │initial  │    └──────────────────┘
│ key_hash    │  │contact  │
│ name        │  │exhausted│
│ role        │  └─────────┘
│ user_id     │
│ is_disabled │
└─────────────┘
```

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
