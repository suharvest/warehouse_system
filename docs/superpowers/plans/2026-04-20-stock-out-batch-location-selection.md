# 出库指定仓库/库位/批次 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 出库操作支持可选指定仓库 / 库位 / 变体 / 批次，未指定时保持现有 FIFO 跨批次拆分行为；REST 前端精确匹配，MCP 因语音输入支持 location 模糊匹配（按产品+仓库作用域）。

**Architecture:** 两层串行模糊（产品名已实现、库位新加作用域模糊）+ `batch_no` 精确指定时绕过 FIFO + 冲突校验（不 fallback）。variant 复用现有 Stage 1 组合索引解析结果，不新增独立通道。前端批次下拉联动产品，复用现有 `GET /api/materials/batches` 接口。

**Tech Stack:** FastAPI / SQLite / rapidfuzz / pypinyin / pytest / 原生 ES modules / Playwright (.mjs)

**Spec:** `docs/superpowers/specs/2026-04-20-stock-out-batch-location-selection-design.md`

---

## 文件结构

**修改：**
- `backend/models.py` — `StockOperationRequest` 增加 `location_fuzzy` 字段
- `backend/fuzzy_match.py` — 新增 `resolve_location_in_scope` 方法
- `backend/app.py` — `stock_out` 端点重构：batch_no 精确分支、location 模糊、variant 继承
- `mcp/providers/base.py` — `stock_out` 抽象方法签名增加 `batch_no`、`location_fuzzy` 参数
- `mcp/providers/default.py` — `stock_out` payload 增加两个参数
- `mcp/warehouse_mcp.py` — MCP `stock_out` 工具签名 + docstring
- `frontend/index.html` — `add-record-modal` 增加批次下拉、变体输入框
- `frontend/src/modules/features/records.js` — 字段显隐、批次联动、提交逻辑
- `frontend/src/modules/api.js` — 增加批次查询 helper（如不存在）
- `frontend/i18n.js` — 新增翻译 key

**测试：**
- `tests/test_fuzzy_match_location.py` — 新增（library-level 测试 `resolve_location_in_scope`）
- `tests/test_stock_out.py` — 扩展（batch_no 指定、冲突、不足、location 模糊、variant 继承）
- `tests/e2e/test_stock_out_modal.py` 或 `.mjs` — 前端出库弹窗联动 Playwright 测试

---

## Task 1: 后端 — `StockOperationRequest.location_fuzzy` 字段

**Files:**
- Modify: `backend/models.py:132-144`

- [ ] **Step 1: 添加字段**

在 `StockOperationRequest` 类末尾（`warehouse_id` 之后）加一行：

```python
class StockOperationRequest(BaseModel):
    """入库/出库请求"""
    product_name: str
    quantity: int
    reason_category: str
    reason_note: Optional[str] = None
    operator: Optional[str] = "MCP系统"
    contact_id: Optional[int] = None
    location: Optional[str] = None
    batch_no: Optional[str] = None  # 入库: 自定义批次号；出库: 指定批次消耗
    variant: Optional[str] = None
    fuzzy: bool = True
    warehouse_id: Optional[int] = None
    location_fuzzy: bool = False  # 出库时对 location 做作用域模糊（仅 MCP 使用）
```

- [ ] **Step 2: 运行现有测试确保不破坏**

```bash
uv run pytest tests/test_stock_out.py tests/test_stock_in.py -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat(models): add location_fuzzy field to StockOperationRequest"
```

---

## Task 2: 后端 — `FuzzyMatcher.resolve_location_in_scope`

**Files:**
- Modify: `backend/fuzzy_match.py`（在 `resolve` 方法后加新方法）
- Test: `tests/test_fuzzy_match_location.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_fuzzy_match_location.py`：

```python
"""Scoped location fuzzy match tests."""
import pytest
from database import get_db_connection


@pytest.fixture()
def scoped_material(admin_client, default_warehouse_id):
    """Material with three batches in distinct locations."""
    import uuid
    from database import get_db_connection

    sku = f"LOC-{uuid.uuid4().hex[:8].upper()}"
    name = f"Location Test Material {sku}"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (name, sku, 'Test', 0, 'pcs', 10, '', default_warehouse_id))
    material_id = cursor.lastrowid
    conn.commit()
    conn.close()

    for loc in ['A-01', 'A-02', 'B-10']:
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": name, "quantity": 10,
            "reason_category": "purchase",
            "warehouse_id": default_warehouse_id,
            "location": loc,
        })
        assert resp.json()['success'] is True

    return {
        'material_id': material_id,
        'warehouse_id': default_warehouse_id,
        'locations': ['A-01', 'A-02', 'B-10'],
    }


class TestResolveLocationInScope:
    def test_exact_match_confident(self, scoped_material):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(
            scoped_material['material_id'],
            scoped_material['warehouse_id'],
            'A-01',
        )
        assert result['confident'] is True
        assert result['best_match']['name'] == 'A-01'

    def test_partial_match_confident(self, scoped_material):
        """'A 区' should resolve to A-01 or A-02 if one clearly wins, else ambiguous."""
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(
            scoped_material['material_id'],
            scoped_material['warehouse_id'],
            'A01',
        )
        assert result['confident'] is True
        assert result['best_match']['name'] == 'A-01'

    def test_ambiguous_returns_candidates(self, scoped_material):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(
            scoped_material['material_id'],
            scoped_material['warehouse_id'],
            'A',
        )
        # 'A' matches both A-01 and A-02 with equal footing
        assert result['confident'] is False
        names = [c['name'] for c in result['candidates']]
        assert 'A-01' in names and 'A-02' in names

    def test_no_match(self, scoped_material):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(
            scoped_material['material_id'],
            scoped_material['warehouse_id'],
            'ZZZZ-999',
        )
        assert result['confident'] is False
        assert result['best_match'] is None

    def test_empty_scope_returns_empty(self, admin_client, default_warehouse_id):
        """Material with no batches → no candidates."""
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        import uuid
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (f"Empty-{uuid.uuid4().hex[:8]}", f"E-{uuid.uuid4().hex[:8]}",
              'Test', 0, 'pcs', default_warehouse_id))
        mid = cursor.lastrowid
        conn.commit()
        conn.close()

        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(mid, default_warehouse_id, 'A-01')
        assert result['confident'] is False
        assert result['best_match'] is None
        assert result['candidates'] == []
```

- [ ] **Step 2: 验证测试失败**

```bash
uv run pytest tests/test_fuzzy_match_location.py -v
```

Expected: FAIL — `AttributeError: 'FuzzyMatcher' object has no attribute 'resolve_location_in_scope'`

- [ ] **Step 3: 实现方法**

在 `backend/fuzzy_match.py` 类末尾（`resolve` 方法之后）添加：

```python
    def resolve_location_in_scope(self, material_id: int, warehouse_id: int,
                                   query: str) -> dict:
        """按产品+仓库作用域对 location 做模糊匹配。

        候选集现场查 SQL（该物料该仓库所有未耗尽批次的 DISTINCT location），
        通常 < 20 条。不走全局索引，避免跨产品污染。

        返回: {best_match, confident, candidates} 同 resolve。
        """
        if not query:
            return {"best_match": None, "confident": False, "candidates": []}

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT DISTINCT location FROM batches
                   WHERE material_id = ? AND warehouse_id = ?
                     AND is_exhausted = 0 AND quantity > 0
                     AND location IS NOT NULL AND location != ''""",
                (material_id, warehouse_id),
            )
            locations = [r['location'] for r in cursor.fetchall()]
        finally:
            conn.close()

        if not locations:
            return {"best_match": None, "confident": False, "candidates": []}

        norm_query = self._normalize(query)
        query_pinyin = self._get_pinyin(norm_query)

        scored = []
        for loc in locations:
            norm_loc = self._normalize(loc)
            loc_pinyin = self._get_pinyin(norm_loc)
            score = self._calc_score(norm_query, query_pinyin, norm_loc, loc_pinyin)
            if score >= 50.0:
                scored.append({
                    "name": loc,
                    "score": round(score, 1),
                    "entity_type": "location",
                    "entity_id": None,
                    "extra": {},
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        if scored:
            top_score = scored[0]["score"]
            scored = [r for r in scored if top_score - r["score"] <= 20]

        if not scored:
            return {"best_match": None, "confident": False, "candidates": []}

        best = scored[0]
        if len(scored) >= 2 and best["score"] == scored[1]["score"]:
            confident = False
        elif len(scored) == 1:
            confident = best["score"] >= 75.0
        elif best["score"] >= 95.0:
            confident = True
        else:
            gap = best["score"] - scored[1]["score"]
            if best["score"] >= 90.0:
                confident = gap > 5.0
            else:
                confident = (best["score"] >= self._confident_score
                             and gap > self._confident_gap)

        return {"best_match": best, "confident": confident, "candidates": scored[:5]}
```

- [ ] **Step 4: 验证测试通过**

```bash
uv run pytest tests/test_fuzzy_match_location.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/fuzzy_match.py tests/test_fuzzy_match_location.py
git commit -m "feat(fuzzy): add resolve_location_in_scope for scoped location fuzzy match"
```

---

## Task 3: 后端 — `stock_out` 指定批次分支（batch_no 精确）

**Files:**
- Modify: `backend/app.py:3017-3217`（整段 `stock_out` 重构）
- Test: `tests/test_stock_out.py`（扩展）

- [ ] **Step 1: 写失败测试 — 指定批次成功出库**

在 `tests/test_stock_out.py` 的 `TestStockOut` 类末尾添加：

```python
    def test_stock_out_specified_batch_success(self, admin_client, stocked_material):
        """指定 batch_no 出库应仅从该批次扣减，不触发 FIFO。"""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 5,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": stocked_material['batch2_no'],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert len(data['batch_consumptions']) == 1
        assert data['batch_consumptions'][0]['batch_no'] == stocked_material['batch2_no']
        assert data['batch_consumptions'][0]['quantity'] == 5

    def test_stock_out_specified_batch_insufficient(self, admin_client, stocked_material):
        """指定批次余量不足应报错，不 fallback 到 FIFO。"""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 999,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": stocked_material['batch1_no'],
        })
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == 'batch_insufficient_stock'

    def test_stock_out_batch_not_found(self, admin_client, stocked_material):
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": "NONEXISTENT-9999",
        })
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == 'batch_not_found'

    def test_stock_out_batch_location_mismatch(self, admin_client, stocked_material):
        """指定 batch_no + 不匹配的 location 应报 batch_field_mismatch。"""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": stocked_material['batch1_no'],
            "location": "WRONG-LOC-ZZ",
        })
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == 'batch_field_mismatch'
```

- [ ] **Step 2: 验证测试失败**

```bash
uv run pytest tests/test_stock_out.py::TestStockOut::test_stock_out_specified_batch_success -v
```

Expected: FAIL (行为未实现，出库会走 FIFO 而非指定批次)

- [ ] **Step 3: 重构 stock_out 端点**

编辑 `backend/app.py` 的 `stock_out` 函数（3017 行起）。在产品名解析块之后、`if stock_data.location or stock_data.variant` 之前，插入"指定批次"分支。整个函数替换为：

```python
@app.post("/api/materials/stock-out", response_model=StockOutResponse)
@limiter.limit("60/minute")
async def stock_out(
    request: Request,
    stock_data: StockOperationRequest,
    current_user: CurrentUser = Depends(require_auth('operate'))
):
    """出库操作（需要operate权限）- FIFO批次消耗，支持模糊匹配、指定批次。"""
    product_name = stock_data.product_name
    quantity = stock_data.quantity
    reason_category = stock_data.reason_category
    reason_note = stock_data.reason_note
    operator = stock_data.operator if stock_data.operator and stock_data.operator != "MCP系统" else current_user.get_operator_name()
    operator_user_id = current_user.id
    resolved_from = None
    resolved_variant = None  # 从 Stage 1 组合索引带出的 variant
    wh_id = require_warehouse_id(current_user, stock_data.warehouse_id)

    if quantity <= 0:
        return StockOutResponse(success=False, error="出库数量必须大于0",
                                message=f"出库失败：数量 {quantity} 无效")

    with get_db() as conn:
        check_warehouse_access(conn, current_user, wh_id)
        cursor = conn.cursor()
        wh_filter, wh_params = build_warehouse_filter(wh_id)

        # 精确匹配
        cursor.execute(f'SELECT id, unit, safe_stock FROM materials WHERE name = ?{wh_filter}',
                       (product_name,) + wh_params)
        row = cursor.fetchone()

        # 模糊匹配
        if not row and stock_data.fuzzy:
            matcher = get_fuzzy_matcher()
            result = matcher.resolve(product_name, entity_type="material")
            if result['confident'] and result['best_match']:
                resolved_from = product_name
                best = result['best_match']
                extra = best.get('extra') or {}
                resolved_variant = extra.get('variant')
                resolved_name = best['name']
                if resolved_variant:
                    resolved_name = resolved_name.replace(f" {resolved_variant}", "").strip()
                product_name = resolved_name
                cursor.execute(f'SELECT id, unit, safe_stock FROM materials WHERE name = ?{wh_filter}',
                               (product_name,) + wh_params)
                row = cursor.fetchone()
            elif result['candidates']:
                names = [c['name'] for c in result['candidates'][:5]]
                return StockOutResponse(
                    success=False, error="ambiguous_name",
                    message=f"无法确定产品 '{product_name}'，候选：{', '.join(names)}",
                    candidates=result['candidates'],
                )

        if not row:
            return StockOutResponse(success=False,
                                    error=f"产品不存在: {product_name}",
                                    message=f"出库失败：未找到产品 '{product_name}'")

        material_id = row['id']
        unit = row['unit']
        safe_stock = row['safe_stock']

        # variant 继承：Stage 1 带出的 variant 且调用方未显式传 → 用之
        effective_variant = stock_data.variant or resolved_variant
        effective_location = stock_data.location

        # location 模糊（仅 MCP 场景开启）
        if stock_data.location_fuzzy and effective_location:
            loc_result = get_fuzzy_matcher().resolve_location_in_scope(
                material_id, wh_id, effective_location)
            if loc_result['confident'] and loc_result['best_match']:
                effective_location = loc_result['best_match']['name']
            elif loc_result['candidates']:
                names = [c['name'] for c in loc_result['candidates'][:5]]
                return StockOutResponse(
                    success=False, error="location_ambiguous",
                    message=f"库位 '{stock_data.location}' 在该产品下匹配多个：{', '.join(names)}",
                    candidates=loc_result['candidates'],
                )
            else:
                cursor.execute(
                    """SELECT DISTINCT location FROM batches
                       WHERE material_id = ? AND warehouse_id = ?
                         AND is_exhausted = 0 AND quantity > 0
                         AND location IS NOT NULL AND location != ''""",
                    (material_id, wh_id))
                avail = [r['location'] for r in cursor.fetchall()]
                return StockOutResponse(
                    success=False, error="location_not_found",
                    message=f"该产品在此仓库下没有匹配 '{stock_data.location}' 的库位。"
                            f"可用库位：{', '.join(avail) if avail else '（无）'}",
                )

        # ─── 分支 A：指定批次精确扣减 ───
        if stock_data.batch_no:
            cursor.execute(
                """SELECT id, batch_no, quantity, location, variant, material_id, warehouse_id
                   FROM batches WHERE batch_no = ?""",
                (stock_data.batch_no,))
            batch = cursor.fetchone()
            if not batch or batch['material_id'] != material_id or batch['warehouse_id'] != wh_id:
                return StockOutResponse(
                    success=False, error="batch_not_found",
                    message=f"批次 '{stock_data.batch_no}' 不存在或不属于当前产品/仓库")

            # 冲突校验
            if effective_location and batch['location'] and effective_location != batch['location']:
                return StockOutResponse(
                    success=False, error="batch_field_mismatch",
                    message=f"批次 {batch['batch_no']} 实际位于库位 "
                            f"'{batch['location']}'，与指定的 '{effective_location}' 不符")
            if effective_variant and batch['variant'] and effective_variant != batch['variant']:
                return StockOutResponse(
                    success=False, error="batch_field_mismatch",
                    message=f"批次 {batch['batch_no']} 实际变体 "
                            f"'{batch['variant']}'，与指定的 '{effective_variant}' 不符")

            if batch['quantity'] < quantity:
                return StockOutResponse(
                    success=False, error="batch_insufficient_stock",
                    message=f"批次 {batch['batch_no']} 余量 {batch['quantity']} {unit}，"
                            f"不足以出库 {quantity} {unit}（不会自动补其它批次）")

            # 扣 materials.quantity（原子更新防并发负库存）
            cursor.execute(
                "UPDATE materials SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
                (quantity, material_id, quantity))
            if cursor.rowcount == 0:
                cursor.execute("SELECT quantity FROM materials WHERE id = ?", (material_id,))
                current_qty = cursor.fetchone()['quantity']
                return StockOutResponse(
                    success=False, error="库存不足",
                    message=f"出库失败：{product_name} 库存 {current_qty} {unit}，"
                            f"不足以出库 {quantity} {unit}")
            new_quantity = cursor.execute("SELECT quantity FROM materials WHERE id = ?",
                                          (material_id,)).fetchone()['quantity']
            old_quantity = new_quantity + quantity

            # 写 inventory_records
            cursor.execute(
                """INSERT INTO inventory_records
                   (material_id, type, quantity, operator, operator_user_id,
                    reason_category, reason_note, contact_id, warehouse_id, created_at)
                   VALUES (?, 'out', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (material_id, quantity, operator, operator_user_id, reason_category,
                 reason_note, stock_data.contact_id, wh_id,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            record_id = cursor.lastrowid

            # 扣该批次
            new_batch_qty = batch['quantity'] - quantity
            is_exhausted = 1 if new_batch_qty == 0 else 0
            cursor.execute("UPDATE batches SET quantity = ?, is_exhausted = ? WHERE id = ?",
                           (new_batch_qty, is_exhausted, batch['id']))
            cursor.execute(
                """INSERT INTO batch_consumptions (record_id, batch_id, quantity, created_at)
                   VALUES (?, ?, ?, ?)""",
                (record_id, batch['id'], quantity,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

            batch_consumptions = [BatchConsumption(
                batch_no=batch['batch_no'], batch_id=batch['id'],
                quantity=quantity, remaining=new_batch_qty, variant=batch['variant'],
            )]
            conn.commit()
            get_fuzzy_matcher().invalidate_cache()

            audit_log("STOCK_OUT", current_user.id, current_user.username, {
                "product": product_name, "quantity": quantity,
                "old_qty": old_quantity, "new_qty": new_quantity,
                "resolved_from": resolved_from,
                "specified_batch": batch['batch_no'],
            })

            warning = ""
            if safe_stock is not None and new_quantity < safe_stock:
                if new_quantity < safe_stock * 0.5:
                    warning = f"⚠️ 警告：库存告急！当前库存 {new_quantity} {unit}，低于安全库存 {safe_stock} {unit} 的50%"
                else:
                    warning = f"⚠️ 提醒：库存偏低，当前库存 {new_quantity} {unit}，低于安全库存 {safe_stock} {unit}"

            return StockOutResponse(
                success=True, operation="stock_out",
                product=StockOperationProduct(
                    name=product_name, old_quantity=old_quantity,
                    out_quantity=quantity, new_quantity=new_quantity,
                    unit=unit, safe_stock=safe_stock,
                ),
                batch_consumptions=batch_consumptions,
                message=f"出库成功：{product_name} 从指定批次 {batch['batch_no']} "
                        f"出库 {quantity} {unit}，库存 {old_quantity}→{new_quantity} {unit}",
                warning=warning if warning else None,
                resolved_from=resolved_from,
            )

        # ─── 分支 B：FIFO（支持 location / variant 过滤） ───
        # 预检查
        if effective_location or effective_variant:
            precheck_sql = """SELECT COALESCE(SUM(quantity), 0) AS avail FROM batches
                              WHERE material_id = ? AND warehouse_id = ?
                                AND is_exhausted = 0 AND quantity > 0"""
            precheck_params = [material_id, wh_id]
            if effective_variant:
                precheck_sql += ' AND variant = ?'
                precheck_params.append(effective_variant)
            if effective_location:
                precheck_sql += ' AND location = ?'
                precheck_params.append(effective_location)
            cursor.execute(precheck_sql, precheck_params)
            avail_qty = cursor.fetchone()['avail']
            if avail_qty < quantity:
                scope = []
                if effective_location:
                    scope.append(f"位置 '{effective_location}'")
                if effective_variant:
                    scope.append(f"变体 '{effective_variant}'")
                return StockOutResponse(
                    success=False, error="库存不足",
                    message=f"出库失败：{product_name} 在 {'、'.join(scope)} "
                            f"的可用库存为 {avail_qty} {unit}，需要出库 {quantity} {unit}")

        cursor.execute(
            "UPDATE materials SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
            (quantity, material_id, quantity))
        if cursor.rowcount == 0:
            cursor.execute("SELECT quantity FROM materials WHERE id = ?", (material_id,))
            current_qty = cursor.fetchone()['quantity']
            return StockOutResponse(
                success=False, error="库存不足",
                message=f"出库失败：{product_name} 库存 {current_qty} {unit}，"
                        f"不足以出库 {quantity} {unit}")
        new_quantity = cursor.execute("SELECT quantity FROM materials WHERE id = ?",
                                      (material_id,)).fetchone()['quantity']
        old_quantity = new_quantity + quantity

        cursor.execute(
            """INSERT INTO inventory_records
               (material_id, type, quantity, operator, operator_user_id,
                reason_category, reason_note, contact_id, warehouse_id, created_at)
               VALUES (?, 'out', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (material_id, quantity, operator, operator_user_id, reason_category,
             reason_note, stock_data.contact_id, wh_id,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        record_id = cursor.lastrowid

        batch_consumptions = []
        remaining_to_consume = quantity
        fifo_sql = """SELECT id, batch_no, quantity, variant, location FROM batches
                      WHERE material_id = ? AND is_exhausted = 0 AND quantity > 0"""
        fifo_params = [material_id]
        if effective_variant:
            fifo_sql += ' AND variant = ?'
            fifo_params.append(effective_variant)
        if effective_location:
            fifo_sql += ' AND location = ?'
            fifo_params.append(effective_location)
        fifo_sql += ' ORDER BY created_at ASC'
        cursor.execute(fifo_sql, fifo_params)

        for b in cursor.fetchall():
            if remaining_to_consume <= 0:
                break
            consume_qty = min(b['quantity'], remaining_to_consume)
            new_batch_qty = b['quantity'] - consume_qty
            remaining_to_consume -= consume_qty
            is_exhausted = 1 if new_batch_qty == 0 else 0
            cursor.execute("UPDATE batches SET quantity = ?, is_exhausted = ? WHERE id = ?",
                           (new_batch_qty, is_exhausted, b['id']))
            cursor.execute(
                """INSERT INTO batch_consumptions (record_id, batch_id, quantity, created_at)
                   VALUES (?, ?, ?, ?)""",
                (record_id, b['id'], consume_qty,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            batch_consumptions.append(BatchConsumption(
                batch_no=b['batch_no'], batch_id=b['id'],
                quantity=consume_qty, remaining=new_batch_qty, variant=b['variant'],
            ))

        conn.commit()
        get_fuzzy_matcher().invalidate_cache()

        audit_log("STOCK_OUT", current_user.id, current_user.username, {
            "product": product_name, "quantity": quantity,
            "old_qty": old_quantity, "new_qty": new_quantity,
            "resolved_from": resolved_from,
            "batches": [bc.batch_no for bc in batch_consumptions],
        })

        warning = ""
        if safe_stock is not None and new_quantity < safe_stock:
            if new_quantity < safe_stock * 0.5:
                warning = f"⚠️ 警告：库存告急！当前库存 {new_quantity} {unit}，低于安全库存 {safe_stock} {unit} 的50%"
            else:
                warning = f"⚠️ 提醒：库存偏低，当前库存 {new_quantity} {unit}，低于安全库存 {safe_stock} {unit}"

        batch_details = ""
        if batch_consumptions:
            details = [f"{bc.batch_no}×{bc.quantity}" for bc in batch_consumptions]
            batch_details = f"（消耗批次: {', '.join(details)}）"

        return StockOutResponse(
            success=True, operation="stock_out",
            product=StockOperationProduct(
                name=product_name, old_quantity=old_quantity,
                out_quantity=quantity, new_quantity=new_quantity,
                unit=unit, safe_stock=safe_stock,
            ),
            batch_consumptions=batch_consumptions if batch_consumptions else None,
            message=f"出库成功：{product_name} 出库 {quantity} {unit}{batch_details}，"
                    f"库存从 {old_quantity} 更新到 {new_quantity} {unit}",
            warning=warning if warning else None,
            resolved_from=resolved_from,
        )
```

- [ ] **Step 4: 运行全部出库测试**

```bash
uv run pytest tests/test_stock_out.py -v
```

Expected: 全部 PASS（新增 4 个测试 + 原有回归）

- [ ] **Step 5: Commit**

```bash
git add backend/app.py tests/test_stock_out.py
git commit -m "feat(stock-out): support batch_no precise consumption and location fuzzy"
```

---

## Task 4: 后端 — location 模糊 & 不足场景的测试

**Files:**
- Test: `tests/test_stock_out.py`（扩展）

- [ ] **Step 1: 添加测试**

在 `TestStockOut` 末尾添加：

```python
    def test_stock_out_location_fuzzy_confident(self, admin_client, default_warehouse_id):
        """location_fuzzy=True 且匹配明确时应成功出库。"""
        import uuid
        name = f"LocFuzzy-{uuid.uuid4().hex[:8]}"

        from database import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO materials
            (name, sku, category, quantity, unit, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (name, f"S-{uuid.uuid4().hex[:8]}", 'Test', 0, 'pcs', default_warehouse_id))
        conn.commit()
        conn.close()

        admin_client.post("/api/materials/stock-in", json={
            "product_name": name, "quantity": 20, "reason_category": "purchase",
            "warehouse_id": default_warehouse_id, "location": "A-01",
        })

        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": name, "quantity": 5, "reason_category": "sell",
            "warehouse_id": default_warehouse_id, "location": "A01",
            "location_fuzzy": True,
        })
        assert resp.json()['success'] is True

    def test_stock_out_location_fuzzy_not_found(self, admin_client, stocked_material):
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'], "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "location": "ZZZ-999", "location_fuzzy": True,
        })
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == 'location_not_found'
```

- [ ] **Step 2: 运行**

```bash
uv run pytest tests/test_stock_out.py -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_stock_out.py
git commit -m "test(stock-out): add location fuzzy success/not_found cases"
```

---

## Task 5: MCP — Provider 签名扩展

**Files:**
- Modify: `mcp/providers/base.py:170-186`
- Modify: `mcp/providers/default.py:170-184`

- [ ] **Step 1: 修改 base 抽象签名**

替换 `mcp/providers/base.py` 的 `stock_out` 抽象方法：

```python
    @abstractmethod
    def stock_out(
        self,
        product_name: str,
        quantity: int,
        reason_category: str,
        reason_note: str,
        operator: str,
        fuzzy: bool,
        variant: str | None = None,
        location: str | None = None,
        batch_no: str | None = None,
        location_fuzzy: bool = False,
    ) -> dict:
        """产品出库。

        batch_no 非空时只从该批次扣减（不足报错，不 fallback）。
        location_fuzzy=True 时对 location 做作用域模糊（仅 MCP 使用）。
        返回: {success, ...}
        """
        ...
```

- [ ] **Step 2: 修改 default provider 实现**

替换 `mcp/providers/default.py` 的 `stock_out` 方法：

```python
    def stock_out(self, product_name, quantity, reason_category, reason_note,
                  operator, fuzzy, variant=None, location=None,
                  batch_no=None, location_fuzzy=False):
        payload = {
            "product_name": product_name,
            "quantity": quantity,
            "reason_category": reason_category,
            "reason_note": reason_note or None,
            "operator": operator,
            "fuzzy": fuzzy,
        }
        if variant is not None:
            payload["variant"] = variant
        if location is not None:
            payload["location"] = location
        if batch_no is not None:
            payload["batch_no"] = batch_no
        if location_fuzzy:
            payload["location_fuzzy"] = True
        return self.http_post("/materials/stock-out", payload)
```

- [ ] **Step 3: Commit**

```bash
git add mcp/providers/base.py mcp/providers/default.py
git commit -m "feat(mcp): extend stock_out provider signature with batch_no and location_fuzzy"
```

---

## Task 6: MCP — `stock_out` 工具 & docstring

**Files:**
- Modify: `mcp/warehouse_mcp.py:227-258`

- [ ] **Step 1: 修改工具函数**

替换 `mcp/warehouse_mcp.py` 的 `stock_out` 函数：

```python
@mcp.tool()
def stock_out(product_name: str, quantity: int,
              reason_category: str, reason_note: str = "",
              operator: str = "MCP系统", fuzzy: bool = True,
              variant: str = None, location: str = None,
              batch_no: str = None) -> dict:
    """
    产品出库。可直接传入模糊名称，自动解析为精确产品。
    默认按 FIFO 消耗批次；若指定 variant / location，则仅从匹配批次中 FIFO 消耗。
    若指定 batch_no，则仅从该批次扣减，不 fallback 到 FIFO。

    参数：
        product_name: 产品名称（支持模糊输入，如"指示灯"会匹配到"LED指示灯"）
        quantity: 出库数量（正整数）
        reason_category: 出库原因分类，必须是以下之一：
            - "sell": 销售出库
            - "use": 领用/消耗
            - "lend": 借出
            - "scrap": 报废
            - "return_out": 退货出库
            - "transfer_out": 调拨出库
            - "other_out": 其他出库
        reason_note: 详情备注（如"销售给XX公司"、"借给小王"），选填
        operator: 操作员姓名（默认"MCP系统"）
        fuzzy: 是否启用产品名模糊匹配（默认 True）
        variant: 变体过滤（可选，如"红"）。指定后仅消耗该变体的批次。精确匹配。
        location: 库位过滤（可选，如"A-01"）。
                  MCP 场景自动开启作用域模糊匹配：用户口述"A 区"可匹配到 A-01。
                  若模糊结果歧义会返回候选让 LLM 判断。
        batch_no: 指定批次号（可选，如"B-2026-003"）。
                  用户明确说"出 B-2026-003 这批"时才传。
                  指定后只从该批次扣，不足直接报错（不 fallback 到 FIFO 补齐）。
                  若同时传 location/variant 与批次实际不符，会报 batch_field_mismatch。

    返回：
        success=true 时：出库成功，含批次消耗详情（每个消耗批次含 variant 字段）
        success=false 时：含具体错误类型，如 ambiguous_name / location_ambiguous /
                         batch_not_found / batch_insufficient_stock / batch_field_mismatch 等
    """
    return _provider.stock_out(product_name, quantity, reason_category, reason_note,
                               operator, fuzzy, variant, location,
                               batch_no=batch_no, location_fuzzy=True)
```

- [ ] **Step 2: 手动验证 MCP 工具仍可加载**

```bash
uv run python -c "from mcp.warehouse_mcp import mcp; print([t.name for t in mcp._tool_manager._tools.values()])"
```

Expected: 输出包含 `stock_out` 的工具列表（无 ImportError / 签名错误）

- [ ] **Step 3: Commit**

```bash
git add mcp/warehouse_mcp.py
git commit -m "feat(mcp): expose batch_no parameter and enable location_fuzzy in stock_out tool"
```

---

## Task 7: 前端 — HTML 模态框新增字段

**Files:**
- Modify: `frontend/index.html:1353-1424`（add-record-modal）

- [ ] **Step 1: 修改 modal 结构**

在 `add-record-modal` 的 `record-location-group` 之后、`</form>` 之前，插入两个新 form-group：

```html
                    <div class="form-group" id="record-variant-group" style="display: none;">
                        <label data-i18n="variant">变体</label>
                        <input type="text" id="record-variant" data-i18n-placeholder="variantPlaceholder" placeholder="如：红、大号（选填）">
                    </div>
                    <div class="form-group" id="record-batch-select-group" style="display: none;">
                        <label data-i18n="outboundBatch">指定批次</label>
                        <select id="record-batch-select">
                            <option value="" data-i18n="autoFIFO">-- FIFO 自动分配 --</option>
                        </select>
                    </div>
```

同时在 `record-location-group` 上把 `style="display: none;"` 的初始值保持——JS 会管控显隐。

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add variant input and batch select to add-record modal"
```

---

## Task 8: 前端 — i18n 新增翻译

**Files:**
- Modify: `frontend/i18n.js`

- [ ] **Step 1: 添加中英文 key**

在 `frontend/i18n.js` 的 zh 段（约第 39 行 `outbound` 附近）添加：

```javascript
        outboundBatch: '指定批次',
        autoFIFO: '-- FIFO 自动分配 --',
        variant: '变体',
        variantPlaceholder: '如：红、大号（选填）',
        batchLoadFailed: '加载批次列表失败',
        batchLocationChipPrefix: '库位：',
        batchVariantChipPrefix: '变体：',
```

在 en 段（约第 416 行 `outbound` 附近）添加：

```javascript
        outboundBatch: 'Specific Batch',
        autoFIFO: '-- Auto (FIFO) --',
        variant: 'Variant',
        variantPlaceholder: 'e.g. Red, Large (optional)',
        batchLoadFailed: 'Failed to load batches',
        batchLocationChipPrefix: 'Location: ',
        batchVariantChipPrefix: 'Variant: ',
```

- [ ] **Step 2: Commit**

```bash
git add frontend/i18n.js
git commit -m "i18n: add strings for outbound batch/variant selection"
```

---

## Task 9: 前端 — records.js 字段显隐 & 批次联动

**Files:**
- Modify: `frontend/src/modules/features/records.js`（改 `updateLocationFieldVisibility`、产品选中回调、`submitAddRecord`）

- [ ] **Step 1: 修改 `updateLocationFieldVisibility`**

替换 `records.js:332-342` 的函数：

```javascript
// 根据操作类型切换字段可见性
function updateLocationFieldVisibility(type) {
    const locationGroup = document.getElementById('record-location-group');
    const batchNoGroup = document.getElementById('record-batch-group'); // 入库用：自定义批次号
    const variantGroup = document.getElementById('record-variant-group');
    const batchSelectGroup = document.getElementById('record-batch-select-group');

    // location 和 variant 两种操作都显示
    if (locationGroup) locationGroup.style.display = 'block';
    if (variantGroup) variantGroup.style.display = 'block';

    // 入库时显示"自定义批次号"输入，出库时显示"指定批次"下拉
    if (batchNoGroup) batchNoGroup.style.display = type === 'in' ? 'block' : 'none';
    if (batchSelectGroup) batchSelectGroup.style.display = type === 'out' ? 'block' : 'none';

    // 切换时重置批次下拉
    if (type === 'out') {
        populateBatchSelectForCurrentProduct();
    } else {
        const sel = document.getElementById('record-batch-select');
        if (sel) sel.value = '';
    }
}
```

- [ ] **Step 2: 新增 `populateBatchSelectForCurrentProduct`**

在 `records.js` 的 `updateLocationFieldVisibility` 之后添加：

```javascript
async function populateBatchSelectForCurrentProduct() {
    const sel = document.getElementById('record-batch-select');
    if (!sel) return;
    sel.innerHTML = `<option value="">${t('autoFIFO')}</option>`;

    const productName = addRecordForProduct
        ? currentProductName
        : document.getElementById('record-product').value;
    if (!productName) return;

    const whId = getCurrentWarehouseId();
    if (!whId) return;

    try {
        const params = new URLSearchParams({ name: productName, warehouse_id: String(whId) });
        const resp = await fetch(`/api/materials/batches?${params.toString()}`, {
            credentials: 'include',
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const batches = data.batches || [];
        for (const b of batches) {
            if (!b.quantity || b.quantity <= 0) continue;
            const parts = [b.batch_no];
            if (b.location) parts.push(b.location);
            if (b.variant) parts.push(b.variant);
            parts.push(`余 ${b.quantity}`);
            const label = parts.join(' · ');
            const opt = document.createElement('option');
            opt.value = b.batch_no;
            opt.textContent = label;
            opt.dataset.location = b.location || '';
            opt.dataset.variant = b.variant || '';
            sel.appendChild(opt);
        }
    } catch (err) {
        console.error('[records] load batches failed:', err);
    }
}
```

确保在文件顶部已经导入 `getCurrentWarehouseId`（如未导入，需补上）。

- [ ] **Step 3: 产品选中时刷新批次下拉**

找到 `showAddRecordModal` 和 `showAddRecordModalForProduct` 中设置产品选择回调的地方（搜索 "onSelect" 或 "product-input" 相关）。如果不存在现成钩子，则在 `records.js` 中查找产品下拉的 `onSelect` 回调并附加调用 `populateBatchSelectForCurrentProduct()`。

搜索 `record-product-input` 在 `records.js` 或 `dropdown.js` 中的回调：

```bash
grep -n "record-product" frontend/src/modules/features/records.js frontend/src/modules/ui/dropdown.js
```

定位到绑定 onSelect 的位置，在设置 `document.getElementById('record-product').value = ...` 之后加：

```javascript
// 切出库模式时刷新批次列表
const recordType = document.querySelector('input[name="record-type"]:checked')?.value;
if (recordType === 'out') {
    populateBatchSelectForCurrentProduct();
}
```

- [ ] **Step 4: 修改 `submitAddRecord` 传递出库新字段**

替换 `records.js:393-445` 相关部分的赋值段为：

```javascript
export async function submitAddRecord() {
    const productName = addRecordForProduct
        ? currentProductName
        : document.getElementById('record-product').value;
    const type = document.querySelector('input[name="record-type"]:checked')?.value;
    const quantity = parseInt(document.getElementById('record-quantity').value);
    const reasonCategory = document.getElementById('record-reason-category').value;
    const reasonNote = document.getElementById('record-reason-note').value.trim() || null;
    const contactId = document.getElementById('record-contact')?.value || null;

    // location / variant：两种操作都读
    const location = document.getElementById('record-location')?.value.trim() || null;
    const variant = document.getElementById('record-variant')?.value.trim() || null;

    // 批次：入库读文本框（自定义批次号），出库读下拉（指定批次）
    let batchNo = null;
    let selectedBatchLocation = null;
    let selectedBatchVariant = null;
    if (type === 'in') {
        const batchNoInput = document.getElementById('record-batch-no');
        batchNo = batchNoInput ? (batchNoInput.value.trim() || null) : null;
    } else {
        const sel = document.getElementById('record-batch-select');
        if (sel && sel.value) {
            batchNo = sel.value;
            const opt = sel.selectedOptions[0];
            selectedBatchLocation = opt?.dataset.location || null;
            selectedBatchVariant = opt?.dataset.variant || null;
        }
    }

    if (!productName || !type || !document.getElementById('record-quantity').value || !reasonCategory) {
        alert(t('fillAllFields'));
        return;
    }
    if (isNaN(quantity) || quantity <= 0) {
        alert(t('quantityMustBePositive'));
        return;
    }

    // 入库时：检查 location 与现有产品库位是否冲突（原逻辑保留）
    if (type === 'in' && location) {
        const product = allProducts.find(p => p.name === productName);
        if (product && product.location && product.location !== location) {
            if (!confirm(`该产品当前库位为「${product.location}」，是否覆盖为「${location}」？`)) return;
        }
    }

    try {
        const requestData = {
            product_name: productName,
            type: type,
            quantity: quantity,
            reason_category: reasonCategory,
            reason_note: reasonNote,
            contact_id: contactId ? parseInt(contactId) : null,
            warehouse_id: getCurrentWarehouseId(),
        };
        // location：入库填的或出库选批次带出的
        const effectiveLocation = location || selectedBatchLocation;
        if (effectiveLocation) requestData.location = effectiveLocation;
        const effectiveVariant = variant || selectedBatchVariant;
        if (effectiveVariant) requestData.variant = effectiveVariant;
        if (batchNo) requestData.batch_no = batchNo;

        const data = await recordsApi.create(requestData);

        if (data.success) {
            alert(data.message);
            closeAddRecordModal();
            if (loadAllProductsFn) loadAllProductsFn();
            if (currentTab === 'records') loadRecords();
            if (currentTab === 'inventory' && loadInventoryFn) loadInventoryFn();
            if (currentTab === 'detail' && currentProductName && loadProductDetailFn) loadProductDetailFn();
            if (currentTab === 'dashboard' && loadDashboardDataFn) loadDashboardDataFn();
        } else {
            alert(data.error || data.message || t('operationFailed'));
        }
    } catch (error) {
        // 401 由全局 session 过期处理
    }
}
```

- [ ] **Step 5: 语法自检**

```bash
cd frontend && node -e "import('./src/modules/features/records.js').then(() => console.log('OK'))"
```

若没有该脚本路径可 import 的环境，起 dev server 并在浏览器控制台查看是否报错：

```bash
cd frontend && python3 -m http.server 8080
```

访问 http://localhost:8080 并查看 console 无语法错误。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/modules/features/records.js
git commit -m "feat(frontend): wire batch dropdown and variant field for outbound"
```

---

## Task 10: 前端 — Playwright E2E 测试

**Files:**
- Create: `frontend/tests/stock_out_batch.mjs`

- [ ] **Step 1: 编写 E2E 测试**

```javascript
// frontend/tests/stock_out_batch.mjs
// 启动后端（uv run uvicorn app:app --port 2124）+ 前端（python -m http.server 8080）后运行：
// node stock_out_batch.mjs
import { chromium } from 'playwright';

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:8080';
const USERNAME = process.env.TEST_USER || 'admin';
const PASSWORD = process.env.TEST_PASS || 'admin';

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 375, height: 812 } });
const page = await ctx.newPage();

await page.goto(FRONTEND);
await page.fill('#login-username', USERNAME);
await page.fill('#login-password', PASSWORD);
await page.click('[data-action="handleLogin"]');
await page.waitForSelector('#add-record-modal', { state: 'attached' });

// 切到记录 tab
await page.click('[data-tab="records"]');
await page.click('[data-action="showAddRecordModal"]');
await page.waitForSelector('#add-record-modal.show');

// 切到出库
await page.click('input[name="record-type"][value="out"]');

// 输出库字段应可见
const locationVisible = await page.isVisible('#record-location-group');
const variantVisible = await page.isVisible('#record-variant-group');
const batchSelectVisible = await page.isVisible('#record-batch-select-group');
const batchNoHidden = !(await page.isVisible('#record-batch-group'));
console.log({ locationVisible, variantVisible, batchSelectVisible, batchNoHidden });

await page.screenshot({ path: 'stock-out-modal-mobile.png', fullPage: true });

// 选产品（假设 DB 已经有一个产品；如无请先通过 API 插入）
// 此处仅做可视验证
await browser.close();

if (!(locationVisible && variantVisible && batchSelectVisible && batchNoHidden)) {
    console.error('FAIL: field visibility mismatch');
    process.exit(1);
}
console.log('OK');
```

- [ ] **Step 2: 运行**

```bash
# 终端1
cd backend && uv run uvicorn app:app --port 2124

# 终端2
cd frontend && python3 -m http.server 8080

# 终端3
cd frontend && node tests/stock_out_batch.mjs
```

Expected: `OK` + 生成 `stock-out-modal-mobile.png`

- [ ] **Step 3: 用 Read 工具查看截图**

确认：
- 库位、变体、指定批次三个字段可见
- 入库批次号输入框隐藏
- 移动端 375x812 视口下布局不溢出

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/stock_out_batch.mjs
git commit -m "test(frontend): e2e for outbound batch select modal"
```

---

## Task 11: 全量回归 & 收尾

- [ ] **Step 1: 运行全部 pytest**

```bash
uv run pytest tests/ -v --ignore=tests/e2e
```

Expected: 全部 PASS，无新增 FAIL

- [ ] **Step 2: 更新 MCP 测试**（如 `tests/test_mcp.py` 调用 `stock_out` 签名）

```bash
grep -n "stock_out" tests/test_mcp.py
```

若有调用，检查参数位置/关键字参数是否兼容新签名（都是可选参数，通常兼容）。

- [ ] **Step 3: 清理、commit**

```bash
git status
git log --oneline wt/task-55--api-fuzzy-match-search-xiaozhi ^main
```

确认 commit 序列清晰可回顾。

---

## 不做（YAGNI）

- 跨批次显式拆分（"从 B001 出 3 + B002 出 2"）
- variant 独立模糊通道（已由 Stage 1 组合索引覆盖）
- 全局 location 索引
- REST 接口开放 `location_fuzzy`（仅 MCP 内部透传）
- 批次下拉改为 autocomplete / 虚拟滚动（批次数通常 <50，原生 select 够用）
