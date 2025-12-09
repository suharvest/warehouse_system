"""
仓库管理系统 FastAPI 后端
"""
from fastapi import FastAPI, Query, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import List, Optional
from io import BytesIO

from database import init_database, generate_mock_data, get_db_connection
from models import (
    DashboardStats, CategoryItem, WeeklyTrend, TopStock, LowStockItem,
    MaterialItem, XiaozhiItem, ProductStats, ProductRecord,
    StockOperationRequest, StockOperationResponse, StockOperationProduct,
    ImportPreviewItem, ExcelImportPreviewResponse, ExcelImportConfirm,
    ExcelImportResponse, ManualRecordRequest
)

# Excel处理
from openpyxl import Workbook, load_workbook

# 创建 FastAPI 应用
app = FastAPI(
    title="仓库管理系统 API",
    description="智能硬件仓库管理系统后端 API",
    version="2.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化数据库
init_database()
generate_mock_data()


# 数据库连接上下文管理器
@contextmanager
def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


# 自定义异常处理（保持响应格式兼容）
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )


# ============ Dashboard APIs ============

@app.get("/api/dashboard/stats", response_model=DashboardStats)
def get_dashboard_stats():
    """获取仪表盘统计数据"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 库存总量
        cursor.execute('SELECT SUM(quantity) as total FROM materials')
        total_stock = cursor.fetchone()['total'] or 0

        # 今日入库量
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cursor.execute('''
            SELECT SUM(quantity) as total
            FROM inventory_records
            WHERE type = 'in' AND created_at >= ?
        ''', (today_start.strftime('%Y-%m-%d %H:%M:%S'),))
        today_in = cursor.fetchone()['total'] or 0

        # 今日出库量
        cursor.execute('''
            SELECT SUM(quantity) as total
            FROM inventory_records
            WHERE type = 'out' AND created_at >= ?
        ''', (today_start.strftime('%Y-%m-%d %H:%M:%S'),))
        today_out = cursor.fetchone()['total'] or 0

        # 库存预警（低于安全库存）
        cursor.execute('''
            SELECT COUNT(*) as count
            FROM materials
            WHERE quantity < safe_stock
        ''')
        low_stock_count = cursor.fetchone()['count']

        # 物料种类数
        cursor.execute('SELECT COUNT(*) as count FROM materials')
        material_types = cursor.fetchone()['count']

        # 计算昨日数据用于百分比变化
        yesterday_start = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_end = today_start

        cursor.execute('''
            SELECT SUM(quantity) as total
            FROM inventory_records
            WHERE type = 'in' AND created_at >= ? AND created_at < ?
        ''', (yesterday_start.strftime('%Y-%m-%d %H:%M:%S'), yesterday_end.strftime('%Y-%m-%d %H:%M:%S')))
        yesterday_in = cursor.fetchone()['total'] or 1

        cursor.execute('''
            SELECT SUM(quantity) as total
            FROM inventory_records
            WHERE type = 'out' AND created_at >= ? AND created_at < ?
        ''', (yesterday_start.strftime('%Y-%m-%d %H:%M:%S'), yesterday_end.strftime('%Y-%m-%d %H:%M:%S')))
        yesterday_out = cursor.fetchone()['total'] or 1

        # 计算百分比变化
        in_change = round(((today_in - yesterday_in) / yesterday_in * 100), 1) if yesterday_in > 0 else 0
        out_change = round(((today_out - yesterday_out) / yesterday_out * 100), 1) if yesterday_out > 0 else 0

        return DashboardStats(
            total_stock=total_stock,
            today_in=today_in,
            today_out=today_out,
            low_stock_count=low_stock_count,
            material_types=material_types,
            in_change=in_change,
            out_change=out_change
        )


@app.get("/api/dashboard/category-distribution", response_model=List[CategoryItem])
def get_category_distribution():
    """获取库存类型分布"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT category, SUM(quantity) as total
            FROM materials
            GROUP BY category
            ORDER BY total DESC
        ''')

        return [
            CategoryItem(name=row['category'], value=row['total'])
            for row in cursor.fetchall()
        ]


@app.get("/api/dashboard/weekly-trend", response_model=WeeklyTrend)
def get_weekly_trend():
    """获取近7天出入库趋势"""
    with get_db() as conn:
        cursor = conn.cursor()

        dates = []
        in_data = []
        out_data = []

        for i in range(6, -1, -1):
            date = datetime.now() - timedelta(days=i)
            date_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
            date_end = date_start + timedelta(days=1)

            dates.append(date.strftime('%m-%d'))

            # 入库数据
            cursor.execute('''
                SELECT SUM(quantity) as total
                FROM inventory_records
                WHERE type = 'in' AND created_at >= ? AND created_at < ?
            ''', (date_start.strftime('%Y-%m-%d %H:%M:%S'), date_end.strftime('%Y-%m-%d %H:%M:%S')))
            in_total = cursor.fetchone()['total'] or 0
            in_data.append(in_total)

            # 出库数据
            cursor.execute('''
                SELECT SUM(quantity) as total
                FROM inventory_records
                WHERE type = 'out' AND created_at >= ? AND created_at < ?
            ''', (date_start.strftime('%Y-%m-%d %H:%M:%S'), date_end.strftime('%Y-%m-%d %H:%M:%S')))
            out_total = cursor.fetchone()['total'] or 0
            out_data.append(out_total)

        return WeeklyTrend(dates=dates, in_data=in_data, out_data=out_data)


@app.get("/api/dashboard/top-stock", response_model=TopStock)
def get_top_stock():
    """获取库存TOP10"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT name, quantity, category
            FROM materials
            ORDER BY quantity DESC
            LIMIT 10
        ''')

        names = []
        quantities = []
        categories = []

        for row in cursor.fetchall():
            names.append(row['name'])
            quantities.append(row['quantity'])
            categories.append(row['category'])

        return TopStock(names=names, quantities=quantities, categories=categories)


@app.get("/api/dashboard/low-stock-alert", response_model=List[LowStockItem])
def get_low_stock_alert():
    """获取库存预警列表"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT name, sku, category, quantity, safe_stock, location
            FROM materials
            WHERE quantity < safe_stock
            ORDER BY (quantity - safe_stock) ASC
            LIMIT 20
        ''')

        return [
            LowStockItem(
                name=row['name'],
                sku=row['sku'],
                category=row['category'],
                quantity=row['quantity'],
                safe_stock=row['safe_stock'],
                location=row['location'],
                shortage=row['safe_stock'] - row['quantity']
            )
            for row in cursor.fetchall()
        ]


# ============ Materials APIs ============

@app.get("/api/materials/xiaozhi", response_model=List[XiaozhiItem])
def get_xiaozhi_stock():
    """获取 watcher-xiaozhi 相关库存"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT name, sku, quantity, unit, category, location
            FROM materials
            WHERE name LIKE '%xiaozhi%' OR name LIKE '%watcher%'
            ORDER BY quantity DESC
        ''')

        return [
            XiaozhiItem(
                name=row['name'],
                sku=row['sku'],
                quantity=row['quantity'],
                unit=row['unit'],
                category=row['category'],
                location=row['location']
            )
            for row in cursor.fetchall()
        ]


@app.get("/api/materials/all", response_model=List[MaterialItem])
def get_all_materials():
    """获取所有库存"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT name, sku, category, quantity, unit, safe_stock, location
            FROM materials
            ORDER BY name ASC
        ''')

        result = []
        for row in cursor.fetchall():
            quantity = row['quantity']
            safe_stock = row['safe_stock']

            # 判断状态
            if quantity >= safe_stock:
                status = 'normal'
                status_text = '正常'
            elif quantity >= safe_stock * 0.5:
                status = 'warning'
                status_text = '偏低'
            else:
                status = 'danger'
                status_text = '告急'

            result.append(MaterialItem(
                name=row['name'],
                sku=row['sku'],
                category=row['category'],
                quantity=quantity,
                unit=row['unit'],
                safe_stock=safe_stock,
                location=row['location'],
                status=status,
                status_text=status_text
            ))

        return result


@app.get("/api/materials/product-stats", response_model=ProductStats)
def get_product_stats(name: str = Query(..., description="产品名称")):
    """获取单个产品的统计数据"""
    if not name:
        raise HTTPException(status_code=400, detail="缺少产品名称参数")

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品基本信息
        cursor.execute('''
            SELECT id, name, sku, quantity, unit, safe_stock, location
            FROM materials
            WHERE name = ?
        ''', (name,))

        product = cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="产品不存在")

        material_id = product['id']
        current_stock = product['quantity']
        unit = product['unit']
        safe_stock = product['safe_stock']

        # 获取今天的日期
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        # 查询今日入库
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'in' AND DATE(created_at) = ?
        ''', (material_id, today))
        today_in = cursor.fetchone()['total']

        # 查询昨日入库
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'in' AND DATE(created_at) = ?
        ''', (material_id, yesterday))
        yesterday_in = cursor.fetchone()['total']

        # 查询今日出库
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'out' AND DATE(created_at) = ?
        ''', (material_id, today))
        today_out = cursor.fetchone()['total']

        # 查询昨日出库
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'out' AND DATE(created_at) = ?
        ''', (material_id, yesterday))
        yesterday_out = cursor.fetchone()['total']

        # 查询总入库和总出库（用于饼图）
        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'in'
        ''', (material_id,))
        total_in = cursor.fetchone()['total']

        cursor.execute('''
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM inventory_records
            WHERE material_id = ? AND type = 'out'
        ''', (material_id,))
        total_out = cursor.fetchone()['total']

        # 计算变化百分比
        in_change = ((today_in - yesterday_in) / yesterday_in * 100) if yesterday_in > 0 else 0
        out_change = ((today_out - yesterday_out) / yesterday_out * 100) if yesterday_out > 0 else 0

        return ProductStats(
            name=name,
            sku=product['sku'],
            current_stock=current_stock,
            unit=unit,
            safe_stock=safe_stock,
            location=product['location'],
            today_in=today_in,
            today_out=today_out,
            in_change=round(in_change, 1),
            out_change=round(out_change, 1),
            total_in=total_in,
            total_out=total_out
        )


@app.get("/api/materials/product-trend", response_model=WeeklyTrend)
def get_product_trend(name: str = Query(..., description="产品名称")):
    """获取单个产品的近7天趋势"""
    if not name:
        raise HTTPException(status_code=400, detail="缺少产品名称参数")

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品ID
        cursor.execute('SELECT id FROM materials WHERE name = ?', (name,))
        product = cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="产品不存在")

        material_id = product['id']

        # 获取近7天的日期
        dates = []
        for i in range(6, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime('%m-%d')
            dates.append(date)

        # 查询每天的入库和出库数据
        in_data = []
        out_data = []

        for i in range(6, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')

            # 查询当天入库
            cursor.execute('''
                SELECT COALESCE(SUM(quantity), 0) as total
                FROM inventory_records
                WHERE material_id = ? AND type = 'in' AND DATE(created_at) = ?
            ''', (material_id, date))
            in_data.append(cursor.fetchone()['total'])

            # 查询当天出库
            cursor.execute('''
                SELECT COALESCE(SUM(quantity), 0) as total
                FROM inventory_records
                WHERE material_id = ? AND type = 'out' AND DATE(created_at) = ?
            ''', (material_id, date))
            out_data.append(cursor.fetchone()['total'])

        return WeeklyTrend(dates=dates, in_data=in_data, out_data=out_data)


@app.get("/api/materials/product-records", response_model=List[ProductRecord])
def get_product_records(name: str = Query(..., description="产品名称")):
    """获取单个产品的出入库记录"""
    if not name:
        raise HTTPException(status_code=400, detail="缺少产品名称参数")

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品ID
        cursor.execute('SELECT id FROM materials WHERE name = ?', (name,))
        product = cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="产品不存在")

        material_id = product['id']

        # 查询最近30条记录
        cursor.execute('''
            SELECT type, quantity, operator, reason, created_at
            FROM inventory_records
            WHERE material_id = ?
            ORDER BY created_at DESC
            LIMIT 30
        ''', (material_id,))

        return [
            ProductRecord(
                type=row['type'],
                quantity=row['quantity'],
                operator=row['operator'],
                reason=row['reason'],
                created_at=row['created_at']
            )
            for row in cursor.fetchall()
        ]


# ============ Stock Operation APIs (for MCP) ============

@app.post("/api/materials/stock-in", response_model=StockOperationResponse)
def stock_in(request: StockOperationRequest):
    """入库操作"""
    product_name = request.product_name
    quantity = request.quantity
    reason = request.reason or "采购入库"
    operator = request.operator or "MCP系统"

    if quantity <= 0:
        return StockOperationResponse(
            success=False,
            error="入库数量必须大于0",
            message=f"入库失败：数量 {quantity} 无效"
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品
        cursor.execute('SELECT id, name, quantity, unit FROM materials WHERE name = ?', (product_name,))
        row = cursor.fetchone()

        if not row:
            return StockOperationResponse(
                success=False,
                error=f"产品不存在: {product_name}",
                message=f"入库失败：未找到产品 '{product_name}'"
            )

        material_id = row['id']
        old_quantity = row['quantity']
        unit = row['unit']
        new_quantity = old_quantity + quantity

        # 更新库存
        cursor.execute('''
            UPDATE materials
            SET quantity = ?
            WHERE id = ?
        ''', (new_quantity, material_id))

        # 记录入库
        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
            VALUES (?, 'in', ?, ?, ?, ?)
        ''', (material_id, quantity, operator, reason, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()

        return StockOperationResponse(
            success=True,
            operation="stock_in",
            product=StockOperationProduct(
                name=product_name,
                old_quantity=old_quantity,
                in_quantity=quantity,
                new_quantity=new_quantity,
                unit=unit
            ),
            message=f"入库成功：{product_name} 入库 {quantity} {unit}，库存从 {old_quantity} 更新到 {new_quantity} {unit}"
        )


@app.post("/api/materials/stock-out", response_model=StockOperationResponse)
def stock_out(request: StockOperationRequest):
    """出库操作"""
    product_name = request.product_name
    quantity = request.quantity
    reason = request.reason or "销售出库"
    operator = request.operator or "MCP系统"

    if quantity <= 0:
        return StockOperationResponse(
            success=False,
            error="出库数量必须大于0",
            message=f"出库失败：数量 {quantity} 无效"
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # 查询产品
        cursor.execute('SELECT id, name, quantity, unit, safe_stock FROM materials WHERE name = ?', (product_name,))
        row = cursor.fetchone()

        if not row:
            return StockOperationResponse(
                success=False,
                error=f"产品不存在: {product_name}",
                message=f"出库失败：未找到产品 '{product_name}'"
            )

        material_id = row['id']
        old_quantity = row['quantity']
        unit = row['unit']
        safe_stock = row['safe_stock']

        # 检查库存是否足够
        if old_quantity < quantity:
            return StockOperationResponse(
                success=False,
                error="库存不足",
                message=f"出库失败：{product_name} 库存不足，当前库存 {old_quantity} {unit}，需要出库 {quantity} {unit}"
            )

        new_quantity = old_quantity - quantity

        # 更新库存
        cursor.execute('''
            UPDATE materials
            SET quantity = ?
            WHERE id = ?
        ''', (new_quantity, material_id))

        # 记录出库
        cursor.execute('''
            INSERT INTO inventory_records (material_id, type, quantity, operator, reason, created_at)
            VALUES (?, 'out', ?, ?, ?, ?)
        ''', (material_id, quantity, operator, reason, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()

        # 检查是否低于安全库存
        warning = ""
        if new_quantity < safe_stock:
            if new_quantity < safe_stock * 0.5:
                warning = f"⚠️ 警告：库存告急！当前库存 {new_quantity} {unit}，低于安全库存 {safe_stock} {unit} 的50%"
            else:
                warning = f"⚠️ 提醒：库存偏低，当前库存 {new_quantity} {unit}，低于安全库存 {safe_stock} {unit}"

        return StockOperationResponse(
            success=True,
            operation="stock_out",
            product=StockOperationProduct(
                name=product_name,
                old_quantity=old_quantity,
                out_quantity=quantity,
                new_quantity=new_quantity,
                unit=unit,
                safe_stock=safe_stock
            ),
            message=f"出库成功：{product_name} 出库 {quantity} {unit}，库存从 {old_quantity} 更新到 {new_quantity} {unit}",
            warning=warning if warning else None
        )


# ============ Excel Import/Export APIs ============

@app.get("/api/materials/export-excel")
def export_materials_excel():
    """导出库存数据为Excel"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, sku, category, quantity, unit, safe_stock, location
            FROM materials
            ORDER BY name ASC
        ''')
        materials = cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "库存数据"

    # 表头
    headers = ['物料名称', '物料编码(SKU)', '类型', '当前库存', '单位', '安全库存', '存放位置']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)

    # 数据
    for row_idx, material in enumerate(materials, 2):
        ws.cell(row=row_idx, column=1, value=material['name'])
        ws.cell(row=row_idx, column=2, value=material['sku'])
        ws.cell(row=row_idx, column=3, value=material['category'])
        ws.cell(row=row_idx, column=4, value=material['quantity'])
        ws.cell(row=row_idx, column=5, value=material['unit'])
        ws.cell(row=row_idx, column=6, value=material['safe_stock'])
        ws.cell(row=row_idx, column=7, value=material['location'])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/materials/import-excel/preview", response_model=ExcelImportPreviewResponse)
async def preview_import_excel(file: UploadFile = File(...)):
    """预览Excel导入内容，计算差异"""
    try:
        contents = await file.read()
        wb = load_workbook(filename=BytesIO(contents))
        ws = wb.active
    except Exception as e:
        return ExcelImportPreviewResponse(
            success=False,
            preview=[],
            new_skus=[],
            total_in=0,
            total_out=0,
            total_new=0,
            message=f"文件解析失败: {str(e)}"
        )

    preview_items = []
    new_skus = []
    total_in = 0
    total_out = 0
    total_new = 0

    with get_db() as conn:
        cursor = conn.cursor()

        for row in ws.iter_rows(min_row=2, values_only=True):
            # 跳过空行
            if not row[1]:  # SKU为空则跳过
                continue

            name = str(row[0]).strip() if row[0] else ""
            sku = str(row[1]).strip()
            category = str(row[2]).strip() if row[2] else "未分类"
            import_qty = int(row[3]) if row[3] is not None else 0
            unit = str(row[4]).strip() if row[4] else "个"
            safe_stock = int(row[5]) if row[5] is not None else 20
            location = str(row[6]).strip() if row[6] else ""

            # 查询当前库存
            cursor.execute('SELECT id, name, quantity FROM materials WHERE sku = ?', (sku,))
            material = cursor.fetchone()

            if material:
                # 已存在的物料
                current_qty = material['quantity']
                difference = import_qty - current_qty

                if difference > 0:
                    operation = 'in'
                    total_in += difference
                elif difference < 0:
                    operation = 'out'
                    total_out += abs(difference)
                else:
                    operation = 'none'

                preview_items.append(ImportPreviewItem(
                    sku=sku,
                    name=material['name'],
                    category=category,
                    unit=unit,
                    safe_stock=safe_stock,
                    location=location,
                    current_quantity=current_qty,
                    import_quantity=import_qty,
                    difference=difference,
                    operation=operation,
                    is_new=False
                ))
            else:
                # 新SKU
                total_new += 1
                new_item = ImportPreviewItem(
                    sku=sku,
                    name=name,
                    category=category,
                    unit=unit,
                    safe_stock=safe_stock,
                    location=location,
                    current_quantity=None,
                    import_quantity=import_qty,
                    difference=import_qty,
                    operation='new',
                    is_new=True
                )
                preview_items.append(new_item)
                new_skus.append(new_item)

    return ExcelImportPreviewResponse(
        success=True,
        preview=preview_items,
        new_skus=new_skus,
        total_in=total_in,
        total_out=total_out,
        total_new=total_new,
        message=f'共解析 {len(preview_items)} 条记录，其中新增 {total_new} 条'
    )


@app.post("/api/materials/import-excel/confirm", response_model=ExcelImportResponse)
def confirm_import_excel(request: ExcelImportConfirm):
    """确认导入，执行变更单"""
    in_count = 0
    out_count = 0
    new_count = 0
    records_created = 0

    with get_db() as conn:
        cursor = conn.cursor()

        for item in request.changes:
            if item.operation == 'none':
                continue

            if item.is_new:
                # 新SKU - 只有当confirm_new_skus为True时才创建
                if not request.confirm_new_skus:
                    continue

                # 创建新物料
                cursor.execute('''
                    INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    item.name,
                    item.sku,
                    item.category or '未分类',
                    item.import_quantity,
                    item.unit or '个',
                    item.safe_stock or 20,
                    item.location or '',
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))

                # 获取新创建物料的ID
                material_id = cursor.lastrowid

                # 如果有初始库存，创建入库记录
                if item.import_quantity != 0:
                    record_type = 'in' if item.import_quantity > 0 else 'out'
                    cursor.execute('''
                        INSERT INTO inventory_records
                        (material_id, type, quantity, operator, reason, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        material_id,
                        record_type,
                        abs(item.import_quantity),
                        request.operator,
                        f"Excel导入: {request.reason} (新建物料)",
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                    records_created += 1

                new_count += 1
            else:
                # 已存在的物料
                cursor.execute('SELECT id, quantity FROM materials WHERE sku = ?', (item.sku,))
                material = cursor.fetchone()

                if not material:
                    continue

                material_id = material['id']
                abs_diff = abs(item.difference)

                if item.operation == 'in':
                    # 入库
                    new_qty = material['quantity'] + abs_diff
                    cursor.execute('UPDATE materials SET quantity = ? WHERE id = ?',
                                 (new_qty, material_id))
                    cursor.execute('''
                        INSERT INTO inventory_records
                        (material_id, type, quantity, operator, reason, created_at)
                        VALUES (?, 'in', ?, ?, ?, ?)
                    ''', (
                        material_id,
                        abs_diff,
                        request.operator,
                        f"Excel导入: {request.reason}",
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                    in_count += 1
                    records_created += 1

                elif item.operation == 'out':
                    # 出库（允许负库存）
                    new_qty = material['quantity'] - abs_diff
                    cursor.execute('UPDATE materials SET quantity = ? WHERE id = ?',
                                 (new_qty, material_id))
                    cursor.execute('''
                        INSERT INTO inventory_records
                        (material_id, type, quantity, operator, reason, created_at)
                        VALUES (?, 'out', ?, ?, ?, ?)
                    ''', (
                        material_id,
                        abs_diff,
                        request.operator,
                        f"Excel导入: {request.reason}",
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                    out_count += 1
                    records_created += 1

        conn.commit()

    return ExcelImportResponse(
        success=True,
        in_count=in_count,
        out_count=out_count,
        new_count=new_count,
        records_created=records_created,
        message=f'导入完成：{in_count}条入库，{out_count}条出库，{new_count}条新增物料'
    )


@app.get("/api/inventory/export-excel")
def export_inventory_records(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    product_name: Optional[str] = Query(None, description="产品名称")
):
    """导出出入库记录为Excel"""
    with get_db() as conn:
        cursor = conn.cursor()

        query = '''
            SELECT m.name, m.sku, r.type, r.quantity, r.operator, r.reason, r.created_at
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            WHERE 1=1
        '''
        params = []

        if start_date:
            query += ' AND DATE(r.created_at) >= ?'
            params.append(start_date)
        if end_date:
            query += ' AND DATE(r.created_at) <= ?'
            params.append(end_date)
        if product_name:
            query += ' AND m.name = ?'
            params.append(product_name)

        query += ' ORDER BY r.created_at DESC'
        cursor.execute(query, params)
        records = cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "出入库记录"

    headers = ['物料名称', '物料编码', '类型', '数量', '操作人', '原因', '时间']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)

    for row_idx, record in enumerate(records, 2):
        ws.cell(row=row_idx, column=1, value=record['name'])
        ws.cell(row=row_idx, column=2, value=record['sku'])
        ws.cell(row=row_idx, column=3, value='入库' if record['type'] == 'in' else '出库')
        ws.cell(row=row_idx, column=4, value=record['quantity'])
        ws.cell(row=row_idx, column=5, value=record['operator'])
        ws.cell(row=row_idx, column=6, value=record['reason'])
        ws.cell(row=row_idx, column=7, value=record['created_at'])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"inventory_records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/inventory/add-record", response_model=StockOperationResponse)
def add_inventory_record(request: ManualRecordRequest):
    """手动新增出入库记录"""
    if request.type == 'in':
        return stock_in(StockOperationRequest(
            product_name=request.product_name,
            quantity=request.quantity,
            reason=request.reason,
            operator=request.operator
        ))
    elif request.type == 'out':
        return stock_out(StockOperationRequest(
            product_name=request.product_name,
            quantity=request.quantity,
            reason=request.reason,
            operator=request.operator
        ))
    else:
        return StockOperationResponse(
            success=False,
            error="无效的操作类型",
            message="类型必须是 'in' 或 'out'"
        )


# ============ 启动配置 ============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=2124)
