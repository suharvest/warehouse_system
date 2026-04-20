# Design: 出库支持指定仓库 / 库位 / 批次

- **Date:** 2026-04-20
- **Branch:** wt/task-55--api-fuzzy-match-search-xiaozhi
- **Scope:** 后端 REST API、MCP 工具、前端出入库弹窗

## 背景

当前出库操作默认按 FIFO 跨批次消耗。仓库通过全局切换器指定，库位/变体虽然后端 API 支持传入做过滤，但前端出库时硬隐藏、变体前端从无入口，批次则完全无法指定（`batch_no` 字段语义仅用于入库自定义）。用户希望在出库时可选地指定仓库、库位、批次，未指定时保持现有 FIFO。

## 数据模型回顾

`batches` 表一行 = 一次入库产生的批次，字段：`batch_no`（全表 UNIQUE）、`material_id`、`warehouse_id`、`location`、`variant`、`quantity`、`is_exhausted`。**一个 `batch_no` 唯一确定 material / warehouse / location / variant**，是最精确的出库定位。

## 功能需求

1. 出库接口（REST + MCP + 前端）新增三个可选过滤维度：
   - `warehouse_id`（已支持，通过前端全局仓库切换器 / MCP API key 自动绑定传入）
   - `location`（后端已支持精确匹配，前端出库时需暴露）
   - `variant`（后端已支持精确匹配，前端出库时需暴露）
   - `batch_no`（新增：出库语义的"指定批次"）
2. 未指定批次时：沿用现有 FIFO 跨批次消耗（可叠加 location/variant 过滤后仍拆分消耗）
3. 指定批次时：仅从该批次扣减，不 fallback 到 FIFO
4. MCP 场景因语音输入不精确，为 `location` 提供模糊匹配支持；REST 前端保持精确

## 核心设计决策

### A. batch_no 优先 + 冲突校验（不 fallback）

指定 `batch_no` 时：
1. 查批次，校验 `material_id` / `warehouse_id` 一致、`is_exhausted=0`
2. 若同时传 `location` 或 `variant`，与批次实际值不符 → 报 `batch_field_mismatch`
3. 余量 < 请求数 → 报 `batch_insufficient_stock`（不补 FIFO）
4. 全部通过后从该批次扣减

理由：用户明确指定批次等于强表达意图，静默 fallback 会出错货；余量不足时用户应自己决定改数量或去掉批次限制走 FIFO。

### B. 串行两层模糊（product → location）

产品名模糊与库位模糊**串行**，互不污染：

```
Stage 1: 产品名模糊（全局索引，复用 fuzzy_match.py 现状）
  confident  → 进 Stage 2
  ambiguous  → 返回产品候选，结束
  not found  → 报错，结束

Stage 2（仅 MCP, location_fuzzy=True）: 库位模糊（按产品+仓库作用域）
  从 batches 表查该 (material_id, warehouse_id) 下 DISTINCT location
  在这个小集合（通常 <20 条）上用 rapidfuzz + pypinyin 打分
  confident  → 落定为精确 location → 走精确过滤 FIFO
  ambiguous  → 返回 location 候选，结束
  not found  → 报错，消息列出该产品现有库位
```

**variant 的模糊**（已有，不重复实现）：
`fuzzy_match.py:69-86` 已把 `"{name} {variant}"` 组合进全局索引（如 "七彩灯 红"）。Stage 1 产品名模糊解析时会把 variant 一并带出，放在 `best_match['extra']['variant']`。stock_out 在 Stage 1 后应：
- 若调用方未显式传 `variant` 且 `extra['variant']` 非空 → 自动作为过滤值
- 若调用方显式传了 variant → 以调用方为准（尊重显式意图）

**不做的事**：
- 不建全局 location 索引（候选集跨产品合并会放大误差）
- 不新增 variant 独立模糊通道（已经通过 Stage 1 的组合索引覆盖）
- REST API 永远不开 `location_fuzzy`，避免前端用户困惑

### C. 前端批次下拉联动

出库时暴露"批次"下拉（而非手输）：

1. 用户选产品 → 触发 `GET /api/materials/{material_id}/batches?warehouse_id=X&available_only=1`（复用/拓展现有 `backend/app.py:2025` 附近的批次查询）
2. 下拉项格式：`B-2026-003 · A-01 · 红 · 余 50 件`
3. 默认值 `-- FIFO 自动分配 --`（不传 batch_no）
4. 选定某批次后，前端同时发送 `batch_no` + `location` + `variant`（冗余，便于后端一致性校验）
5. 出库时仍保留独立的"库位"、"变体"输入框（用户可以不选批次只按库位/变体过滤 FIFO）
6. 切换到"入库" radio → 恢复现有入库字段行为（batch_no 是自定义批次号，location 是入库位置）

## 实现清单

### 后端 (`backend/`)

**`models.py`**
- `StockOperationRequest` 新增 `location_fuzzy: bool = False`
- 文档 `batch_no` 在出/入库两种语义

**`app.py` - `POST /api/materials/stock-out` (stock_out)**
- 分支 1：`stock_data.batch_no` 非空 → 走新函数 `_consume_specified_batch()`
  - 按 `batch_no` 查批次，校验归属 + 冲突 + 余量
  - 扣 `materials.quantity` 原子更新
  - 扣该批次 `batches.quantity`、写 `batch_consumptions`、写 `inventory_records`
- 分支 2：`batch_no` 为空 → 现有 FIFO 逻辑（不变），但先做 location 模糊 + variant 继承（下）
- 在精确过滤前：
  - 若 Stage 1 产品名走了模糊，且 `best_match['extra']['variant']` 非空 且调用方没传 `variant` → 自动填充为过滤值
  - 若 `location_fuzzy=True` 且 `location` 非空 → 调 `resolve_location_in_scope(material_id, warehouse_id, location)` 解析为精确值，ambiguous / not_found 直接 return

**`fuzzy_match.py`**
- 新公开方法 `resolve_location_in_scope(material_id: int, warehouse_id: int, query: str) -> dict`
  - 现场 SQL 取候选集
  - 复用 `_calc_score` + 置信判定
  - 返回结构：`{"confident": bool, "best_match": {...}, "candidates": [...]}`

**批次查询 API**
- 复用或新增 `GET /api/materials/{material_id}/batches`
  - 查询参数：`warehouse_id`（必填写操作场景）、`available_only`（默认 true，过滤 `is_exhausted=0 AND quantity > 0`）
  - 返回：`[{batch_no, location, variant, quantity, created_at}]`

### MCP (`mcp/`)

**`warehouse_mcp.py` - `stock_out()`**
- 新增参数 `batch_no: str = None`
- 调底层 provider 时固定 `location_fuzzy=True`
- Docstring 写清楚：
  - `batch_no`: LLM 在用户明确说"出 B001 这批"时才传
  - `location`: 允许模糊（如"A 区" → 匹配 A-01）
  - `variant`: 精确匹配（如"红"）

**`providers/base.py` / `providers/default.py`**
- `stock_out()` 签名增加 `batch_no` + `location_fuzzy` 参数

### 前端 (`frontend/`)

**`index.html`**
- `add-record-modal`：
  - 新增"批次"下拉 `<select id="record-batch-select">`（出库显示）
  - 新增"变体"输入 `<input id="record-variant">`（出入库都显示，选填）
  - 原 `record-batch-no` 保留（入库用于自定义批次号）
  - 原 `record-location` 保留（入库 + 出库都显示）

**`src/modules/features/records.js`**
- `updateLocationFieldVisibility(type)`：
  - 出库：显示 location、variant、batch-select（隐藏 batch-no 输入）
  - 入库：显示 location、variant、batch-no 输入（隐藏 batch-select）
- 产品选中回调：出库时拉取 `materials/{id}/batches`，填充 `batch-select`
- `submitAddRecord()`：
  - 出库 → 读 batch-select 的值（选中时）传 `batch_no`，叠加 location/variant
  - 选中某批次时，前端把对应的 location/variant 也带上（便于后端校验冲突，但真正的精确控制以 batch_no 为准）

**`src/modules/api.js`**
- 新增 `materialsApi.getBatches(materialId, warehouseId)`

**`frontend/i18n.js`**
- 新增 key：`selectBatch`、`autoFIFO`、`variant`、`batchLocationMismatch` 等

## 错误类型一览

| error | 触发条件 | 响应含 |
|---|---|---|
| `product_ambiguous` | Stage 1 多候选 | `candidates: [产品候选]` |
| `product_not_found` | Stage 1 无命中 | — |
| `location_ambiguous` | Stage 2 多候选 | `candidates: [location 候选]` |
| `location_not_found` | Stage 2 无命中 | `available_locations: [...]` |
| `batch_field_mismatch` | 指定 batch_no 但 location/variant/warehouse 不符 | `conflict: {field, expected, got}` |
| `batch_insufficient_stock` | 指定 batch_no 余量不足 | `batch_no, available, requested` |
| `batch_not_found` | batch_no 不存在或不属于该仓库/产品 | — |
| `insufficient_stock` | FIFO 过滤后总量不足（原有，不变） | — |

## 行为矩阵

| batch_no | location | variant | 行为 |
|---|---|---|---|
| 未传 | 未传 | 未传 | 全产品 FIFO（现状） |
| 未传 | 已传 | 未传 | 按 location 过滤后 FIFO 拆分 |
| 未传 | 未传 | 已传 | 按 variant 过滤后 FIFO 拆分 |
| 未传 | 已传 | 已传 | location ∩ variant 过滤后 FIFO 拆分 |
| 已传 | 未传 | 未传 | 仅该批次扣减，不足报错 |
| 已传 | 已传 | — | 校验批次实际 location 一致，否则 `batch_field_mismatch` |
| 已传 | — | 已传 | 同上校验 variant |

## 测试计划

- 后端单元测试：`_consume_specified_batch` 各冲突 / 不足场景
- 后端集成测试：FIFO 跨批次拆分行为不变（回归）
- MCP 测试：`stock_out` 带 `batch_no`、模糊 location 命中 / 歧义 / 未命中
- 前端 Playwright：
  - 出库弹窗批次下拉联动选品后刷新
  - 选批次后 location/variant 带出且只读
  - 提交后记录正确
  - 移动端 375x812 视口适配

## 不做的事（YAGNI）

- 不做跨批次显式拆分（"从 B001 出 3 个 + 从 B002 出 2 个"）
- 不做 variant 独立模糊匹配（已被 Stage 1 组合索引覆盖）
- 不做全局 location 索引
- 不改 `batch_no` 字段名 / 拆分成 in/out 两个字段
- 不做模糊 location 的 REST API 开关（只 MCP 用）
