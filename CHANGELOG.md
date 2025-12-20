# 更新记录

## 2025-12-20 v3.0.0

本次升级为仓库管理系统添加了三个核心功能模块：用户权限管理、联系方管理、批次管理。

### 用户管理与权限控制

**新增数据库表**
- `users`: 用户账户（用户名、密码哈希、角色、显示名称）
- `sessions`: 会话管理（令牌、过期时间）
- `api_keys`: API密钥（用于MCP终端身份识别）

**权限级别**
| 角色 | 权限范围 |
|------|----------|
| `view` | 只读访问所有数据 |
| `operate` | view + 入库/出库/导入/导出/管理联系方 |
| `admin` | operate + 用户管理/API密钥管理 |
| 访客 | 等同view，无需登录 |

**新增API**
- 认证相关: `GET /api/auth/status`, `POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
- 用户管理: `GET/POST/PUT/DELETE /api/users` (admin权限)
- API密钥: `GET/POST/DELETE /api/api-keys` (admin权限)

**前端变更**
- 登录/注册模态框
- 首次设置管理员流程
- 用户管理TAB（仅admin可见）
- API密钥管理界面
- `data-min-role` 属性控制按钮可见性
- 头部显示当前用户状态

### 联系方管理

**新增数据库表**
- `contacts`: 联系方（供应商/客户）信息

**功能特性**
- 联系方TAB（第5个标签页）
- 联系方CRUD模态框
- 入库/出库时可选择联系方（入库→供应商，出库→客户）
- 进出库记录表格显示联系方
- Excel导出包含"联系方"列

**新增API**
- `GET /api/contacts`: 联系方列表（分页+筛选）
- `GET /api/contacts/suppliers`: 供应商下拉列表
- `GET /api/contacts/customers`: 客户下拉列表
- `POST/PUT/DELETE /api/contacts`: 联系方CRUD (operate权限)

### 批次管理

**新增数据库表**
- `batches`: 批次记录（批次号、剩余数量、初始数量、供应商）
- `batch_consumptions`: 批次消耗记录（出库时FIFO消耗详情）

**批次号格式**
- 格式: `YYYYMMDD-XXX`（如 20251220-001）
- 每日从001开始，每个物料独立计数

**FIFO出库算法**
1. 获取该物料未耗尽批次，按 `created_at` ASC 排序
2. 从最早批次开始消耗，直到满足出库数量
3. 更新批次剩余数量，记录消耗明细
4. 若批次耗尽则标记 `is_exhausted = 1`

**API响应变更**
| API | 新增响应字段 |
|-----|-------------|
| `stock_in` | `batch: {batch_no, batch_id, quantity}` |
| `stock_out` | `batch_consumptions: [{batch_no, batch_id, quantity, remaining}]` |
| `inventory/records` | `batch_id, batch_no, batch_details` |

**前端变更**
- 进出库记录表格新增"批次"列
- 入库记录显示批次号
- 出库记录显示批次消耗详情（如 `20251220-001×30, 20251220-002×20`）
- Excel导出包含批次信息

### 数据库架构图

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

### UI/UX 优化
- **界面美化**：全新暖色调主题 (`#f4f7f0`)，圆角卡片设计
- **布局优化**：分页控件集成至表格卡片，更紧凑
- **必填校验**：表单增加红星标记，提交时强制校验

### 功能增强
- **导出筛选**：Excel导出功能支持按时间、名称、分类、状态筛选
- **Excel导入**：
  - 支持“禁用未出现SKU”选项
  - 自动更新安全库存、单位等属性
  - 导入差异预览与确认
- **返回导航**：详情页增加返回列表按钮，保留筛选状态

## v2.0.0

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

## v1.1.0

### 功能特性
- **多语言支持**：中英文切换功能
- 修复库存列表与 TOP10 图表间距问题
