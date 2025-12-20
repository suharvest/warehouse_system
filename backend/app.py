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
    ExcelImportResponse, ManualRecordRequest,
    PaginatedMaterialsResponse, PaginatedRecordsResponse, MaterialItemWithDisabled,
    InventoryRecordItem, PaginatedProductRecordsResponse
)
import math

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
    """获取仪表盘统计数据（排除禁用物料）"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 库存总量（排除禁用）
        cursor.execute('SELECT SUM(quantity) as total FROM materials WHERE is_disabled = 0')
        total_stock = cursor.fetchone()['total'] or 0

        # 今日入库量（排除禁用物料的记录）
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cursor.execute('''
            SELECT SUM(r.quantity) as total
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            WHERE r.type = 'in' AND r.created_at >= ? AND m.is_disabled = 0
        ''', (today_start.strftime('%Y-%m-%d %H:%M:%S'),))
        today_in = cursor.fetchone()['total'] or 0

        # 今日出库量（排除禁用物料的记录）
        cursor.execute('''
            SELECT SUM(r.quantity) as total
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            WHERE r.type = 'out' AND r.created_at >= ? AND m.is_disabled = 0
        ''', (today_start.strftime('%Y-%m-%d %H:%M:%S'),))
        today_out = cursor.fetchone()['total'] or 0

        # 库存预警（低于安全库存，排除禁用）
        cursor.execute('''
            SELECT COUNT(*) as count
            FROM materials
            WHERE quantity < safe_stock AND is_disabled = 0
        ''')
        low_stock_count = cursor.fetchone()['count']

        # 物料种类数（排除禁用）
        cursor.execute('SELECT COUNT(*) as count FROM materials WHERE is_disabled = 0')
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
    """获取所有库存（兼容旧API）"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT name, sku, category, quantity, unit, safe_stock, location, is_disabled
            FROM materials
            WHERE is_disabled = 0
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


@app.get("/api/materials/list", response_model=PaginatedMaterialsResponse)
def get_materials_list(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=10, le=100, description="每页条数"),
    name: Optional[str] = Query(None, description="名称/SKU模糊搜索"),
    category: Optional[str] = Query(None, description="分类"),
    status: Optional[str] = Query(None, description="状态(逗号分隔: normal,warning,danger,disabled)")
):
    """获取物料列表（分页+筛选）"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 解析状态筛选
        status_filter = status.split(',') if status else None

        # 构建基础查询
        base_query = '''
            SELECT name, sku, category, quantity, unit, safe_stock, location, is_disabled
            FROM materials
            WHERE 1=1
        '''
        count_query = 'SELECT COUNT(*) as total FROM materials WHERE 1=1'
        params = []

        # 如果没有指定状态筛选，或者状态筛选中不包含disabled，则只查询未禁用的
        if not status_filter or 'disabled' not in status_filter:
            base_query += ' AND is_disabled = 0'
            count_query += ' AND is_disabled = 0'

        # 名称/SKU搜索
        if name:
            base_query += ' AND (name LIKE ? OR sku LIKE ?)'
            count_query += ' AND (name LIKE ? OR sku LIKE ?)'
            params.extend([f'%{name}%', f'%{name}%'])

        # 分类筛选
        if category:
            base_query += ' AND category = ?'
            count_query += ' AND category = ?'
            params.append(category)

        # 获取总数
        cursor.execute(count_query, params)
        total = cursor.fetchone()['total']

        # 排序和分页
        base_query += ' ORDER BY name ASC LIMIT ? OFFSET ?'
        offset = (page - 1) * page_size
        params.extend([page_size, offset])

        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        result = []
        for row in rows:
            quantity = row['quantity']
            safe_stock = row['safe_stock']
            is_disabled = bool(row['is_disabled'])

            # 判断状态
            if is_disabled:
                item_status = 'disabled'
                status_text = '禁用'
            elif quantity >= safe_stock:
                item_status = 'normal'
                status_text = '正常'
            elif quantity >= safe_stock * 0.5:
                item_status = 'warning'
                status_text = '偏低'
            else:
                item_status = 'danger'
                status_text = '告急'

            # 状态筛选
            if status_filter and item_status not in status_filter:
                continue

            result.append(MaterialItemWithDisabled(
                name=row['name'],
                sku=row['sku'],
                category=row['category'],
                quantity=quantity,
                unit=row['unit'],
                safe_stock=safe_stock,
                location=row['location'],
                status=item_status,
                status_text=status_text,
                is_disabled=is_disabled
            ))

        # 如果有状态筛选，重新计算总数
        if status_filter:
            # 需要重新查询不带分页的结果来计算正确的总数
            base_query_no_limit = base_query.replace(' LIMIT ? OFFSET ?', '')
            cursor.execute(base_query_no_limit, params[:-2])
            all_rows = cursor.fetchall()
            filtered_count = 0
            for row in all_rows:
                quantity = row['quantity']
                safe_stock = row['safe_stock']
                is_disabled = bool(row['is_disabled'])

                if is_disabled:
                    item_status = 'disabled'
                elif quantity >= safe_stock:
                    item_status = 'normal'
                elif quantity >= safe_stock * 0.5:
                    item_status = 'warning'
                else:
                    item_status = 'danger'

                if item_status in status_filter:
                    filtered_count += 1
            total = filtered_count

        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return PaginatedMaterialsResponse(
            items=result,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages
        )


@app.get("/api/materials/categories", response_model=List[str])
def get_categories():
    """获取所有物料分类"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT category FROM materials ORDER BY category')
        return [row['category'] for row in cursor.fetchall()]


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


@app.get("/api/materials/product-records", response_model=PaginatedProductRecordsResponse)
def get_product_records(
    name: str = Query(..., description="产品名称"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=10, le=100, description="每页条数")
):
    """获取单个产品的出入库记录（分页）"""
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

        # 获取总数
        cursor.execute('SELECT COUNT(*) as total FROM inventory_records WHERE material_id = ?', (material_id,))
        total = cursor.fetchone()['total']

        # 分页查询
        offset = (page - 1) * page_size
        cursor.execute('''
            SELECT type, quantity, operator, reason, created_at
            FROM inventory_records
            WHERE material_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        ''', (material_id, page_size, offset))

        items = [
            ProductRecord(
                type=row['type'],
                quantity=row['quantity'],
                operator=row['operator'],
                reason=row['reason'],
                created_at=row['created_at']
            )
            for row in cursor.fetchall()
        ]

        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return PaginatedProductRecordsResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages
        )


@app.get("/api/inventory/records", response_model=PaginatedRecordsResponse)
def get_inventory_records_paginated(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=10, le=100, description="每页条数"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    product_name: Optional[str] = Query(None, description="产品名称/SKU模糊搜索"),
    category: Optional[str] = Query(None, description="商品类型/分类"),
    record_type: Optional[str] = Query(None, description="记录类型: in/out"),
    status: Optional[str] = Query(None, description="状态(逗号分隔: normal,warning,danger,disabled)")
):
    """获取所有进出库记录（分页+筛选）"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 解析状态筛选
        status_filter = status.split(',') if status else None

        # 构建查询
        base_query = '''
            SELECT r.id, m.name as material_name, m.sku as material_sku, m.category,
                   r.type, r.quantity, r.operator, r.reason, r.created_at,
                   m.quantity as current_quantity, m.safe_stock, m.is_disabled
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            WHERE 1=1
        '''
        count_query = '''
            SELECT COUNT(*) as total
            FROM inventory_records r
            JOIN materials m ON r.material_id = m.id
            WHERE 1=1
        '''
        params = []

        # 时间范围筛选
        if start_date:
            base_query += ' AND DATE(r.created_at) >= ?'
            count_query += ' AND DATE(r.created_at) >= ?'
            params.append(start_date)
        if end_date:
            base_query += ' AND DATE(r.created_at) <= ?'
            count_query += ' AND DATE(r.created_at) <= ?'
            params.append(end_date)

        # 产品名称/SKU搜索
        if product_name:
            base_query += ' AND (m.name LIKE ? OR m.sku LIKE ?)'
            count_query += ' AND (m.name LIKE ? OR m.sku LIKE ?)'
            params.extend([f'%{product_name}%', f'%{product_name}%'])

        # 商品类型/分类筛选
        if category:
            base_query += ' AND m.category = ?'
            count_query += ' AND m.category = ?'
            params.append(category)

        # 记录类型筛选
        if record_type:
            base_query += ' AND r.type = ?'
            count_query += ' AND r.type = ?'
            params.append(record_type)

        # 获取总数
        cursor.execute(count_query, params)
        total = cursor.fetchone()['total']

        # 排序和分页
        base_query += ' ORDER BY r.created_at DESC LIMIT ? OFFSET ?'
        offset = (page - 1) * page_size
        params.extend([page_size, offset])

        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        result = []
        filtered_count = 0
        for row in rows:
            quantity = row['current_quantity']
            safe_stock = row['safe_stock']
            is_disabled = bool(row['is_disabled'])

            # 计算物料当前状态
            if is_disabled:
                material_status = 'disabled'
            elif quantity >= safe_stock:
                material_status = 'normal'
            elif quantity >= safe_stock * 0.5:
                material_status = 'warning'
            else:
                material_status = 'danger'

            # 状态筛选
            if status_filter and material_status not in status_filter:
                continue

            result.append(InventoryRecordItem(
                id=row['id'],
                material_name=row['material_name'],
                material_sku=row['material_sku'],
                category=row['category'],
                type=row['type'],
                quantity=row['quantity'],
                operator=row['operator'],
                reason=row['reason'],
                created_at=row['created_at'],
                material_status=material_status,
                is_disabled=is_disabled
            ))
            filtered_count += 1

        # 如果有状态筛选，需要重新计算总数
        if status_filter:
            # 需要遍历所有数据来计算真实的筛选后总数
            count_base_query = '''
                SELECT m.quantity, m.safe_stock, m.is_disabled
                FROM inventory_records r
                JOIN materials m ON r.material_id = m.id
                WHERE 1=1
            '''
            count_params = []
            if start_date:
                count_base_query += ' AND DATE(r.created_at) >= ?'
                count_params.append(start_date)
            if end_date:
                count_base_query += ' AND DATE(r.created_at) <= ?'
                count_params.append(end_date)
            if product_name:
                count_base_query += ' AND (m.name LIKE ? OR m.sku LIKE ?)'
                count_params.extend([f'%{product_name}%', f'%{product_name}%'])
            if record_type:
                count_base_query += ' AND r.type = ?'
                count_params.append(record_type)

            cursor.execute(count_base_query, count_params)
            all_rows = cursor.fetchall()
            total = 0
            for r in all_rows:
                qty = r['quantity']
                ss = r['safe_stock']
                dis = bool(r['is_disabled'])
                if dis:
                    s = 'disabled'
                elif qty >= ss:
                    s = 'normal'
                elif qty >= ss * 0.5:
                    s = 'warning'
                else:
                    s = 'danger'
                if s in status_filter:
                    total += 1

        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return PaginatedRecordsResponse(
            items=result,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages
        )


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

        # 查询产品（获取单位用于展示）
        cursor.execute('SELECT id, unit FROM materials WHERE name = ?', (product_name,))
        row = cursor.fetchone()

        if not row:
            return StockOperationResponse(
                success=False,
                error=f"产品不存在: {product_name}",
                message=f"入库失败：未找到产品 '{product_name}'"
            )

        material_id = row['id']
        unit = row['unit']

        # 原子化更新，避免并发覆盖
        cursor.execute('''
            UPDATE materials
            SET quantity = quantity + ?
            WHERE id = ?
        ''', (quantity, material_id))
        if cursor.rowcount == 0:
            return StockOperationResponse(
                success=False,
                error="入库失败",
                message="入库操作未生效，请重试"
            )

        # 获取更新后的库存，反推更新前数值用于响应
        cursor.execute('SELECT quantity FROM materials WHERE id = ?', (material_id,))
        new_quantity = cursor.fetchone()['quantity']
        old_quantity = new_quantity - quantity

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
        cursor.execute('SELECT id, unit, safe_stock FROM materials WHERE name = ?', (product_name,))
        row = cursor.fetchone()

        if not row:
            return StockOperationResponse(
                success=False,
                error=f"产品不存在: {product_name}",
                message=f"出库失败：未找到产品 '{product_name}'"
            )

        material_id = row['id']
        unit = row['unit']
        safe_stock = row['safe_stock']

        # 原子化更新，防止并发扣减导致负库存
        cursor.execute('''
            UPDATE materials
            SET quantity = quantity - ?
            WHERE id = ? AND quantity >= ?
        ''', (quantity, material_id, quantity))

        if cursor.rowcount == 0:
            # 查询当前库存以返回提示
            cursor.execute('SELECT quantity FROM materials WHERE id = ?', (material_id,))
            current_qty_row = cursor.fetchone()
            current_qty = current_qty_row['quantity'] if current_qty_row else 0
            return StockOperationResponse(
                success=False,
                error="库存不足",
                message=f"出库失败：{product_name} 库存不足，当前库存 {current_qty} {unit}，需要出库 {quantity} {unit}"
            )

        cursor.execute('SELECT quantity FROM materials WHERE id = ?', (material_id,))
        new_quantity = cursor.fetchone()['quantity']
        old_quantity = new_quantity + quantity

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
def export_materials_excel(
    name: Optional[str] = Query(None, description="名称/SKU模糊搜索"),
    category: Optional[str] = Query(None, description="分类"),
    status: Optional[str] = Query(None, description="状态(逗号分隔)")
):
    """导出库存数据为Excel（支持筛选）"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # 基础查询
        query = '''
            SELECT name, sku, category, quantity, unit, safe_stock, location, is_disabled
            FROM materials
            WHERE 1=1
        '''
        params = []
        
        # 解析状态筛选
        status_filter = status.split(',') if status else None

        # 如果没有指定状态筛选，或者状态筛选中不包含disabled，则只查询未禁用的
        if not status_filter or 'disabled' not in status_filter:
            query += ' AND is_disabled = 0'

        # 名称/SKU搜索
        if name:
            query += ' AND (name LIKE ? OR sku LIKE ?)'
            params.extend([f'%{name}%', f'%{name}%'])

        # 分类筛选
        if category:
            query += ' AND category = ?'
            params.append(category)

        query += ' ORDER BY name ASC'
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
    # 需要在应用层过滤状态，因为状态是动态计算的
    materials = []
    for row in rows:
        quantity = row['quantity']
        safe_stock = row['safe_stock']
        is_disabled = bool(row['is_disabled'])

        # 计算状态
        if is_disabled:
            item_status = 'disabled'
            status_text = '禁用'
        elif quantity >= safe_stock:
            item_status = 'normal'
            status_text = '正常'
        elif quantity >= safe_stock * 0.5:
            item_status = 'warning'
            status_text = '偏低'
        else:
            item_status = 'danger'
            status_text = '告急'

        # 状态筛选
        if status_filter and item_status not in status_filter:
            continue
            
        materials.append({
            'name': row['name'],
            'sku': row['sku'],
            'category': row['category'],
            'quantity': row['quantity'],
            'unit': row['unit'],
            'safe_stock': row['safe_stock'],
            'location': row['location'],
            'status_text': status_text
        })

    wb = Workbook()
    ws = wb.active
    ws.title = "库存数据"

    # 表头
    headers = ['物料名称', '物料编码(SKU)', '分类', '状态', '当前库存', '单位', '安全库存', '存放位置']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)

    # 数据
    for row_idx, material in enumerate(materials, 2):
        ws.cell(row=row_idx, column=1, value=material['name'])
        ws.cell(row=row_idx, column=2, value=material['sku'])
        ws.cell(row=row_idx, column=3, value=material['category'])
        ws.cell(row=row_idx, column=4, value=material['status_text'])
        ws.cell(row=row_idx, column=5, value=material['quantity'])
        ws.cell(row=row_idx, column=6, value=material['unit'])
        ws.cell(row=row_idx, column=7, value=material['safe_stock'])
        ws.cell(row=row_idx, column=8, value=material['location'])

    # 设置列宽
    column_widths = [22, 18, 14, 10, 12, 8, 12, 14]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width

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

        for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            # 跳过空行
            if not row[1]:  # SKU为空则跳过
                continue

            name = str(row[0]).strip() if row[0] else ""
            sku = str(row[1]).strip()
            category = str(row[2]).strip() if row[2] else "未分类"
            try:
                import_qty = int(row[3]) if row[3] is not None else 0
            except (ValueError, TypeError):
                return ExcelImportPreviewResponse(
                    success=False,
                    preview=[],
                    new_skus=[],
                    total_in=0,
                    total_out=0,
                    total_new=0,
                    message=f"第 {idx} 行【导入数量】格式错误：需要整数，当前值为 '{row[3]}'，请修改后重新上传"
                )

            unit = str(row[4]).strip() if row[4] else "个"
            try:
                safe_stock = int(row[5]) if row[5] is not None else 20
            except (ValueError, TypeError):
                return ExcelImportPreviewResponse(
                    success=False,
                    preview=[],
                    new_skus=[],
                    total_in=0,
                    total_out=0,
                    total_new=0,
                    message=f"第 {idx} 行【安全库存】格式错误：需要整数，当前值为 '{row[5]}'，请修改后重新上传"
                )
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
    warnings = []

    with get_db() as conn:
        cursor = conn.cursor()

        # 收集导入文件中的所有SKU
        import_skus = set(item.sku for item in request.changes)

        # 将不在导入文件中的SKU标记为禁用（需显式确认）
        if import_skus:
            placeholders = ','.join(['?' for _ in import_skus])
            if request.confirm_disable_missing_skus:
                cursor.execute(f'''
                    UPDATE materials SET is_disabled = 1
                    WHERE sku NOT IN ({placeholders})
                ''', list(import_skus))
            else:
                warnings.append("已跳过禁用导入文件之外的SKU，如需禁用请勾选确认选项后重试。")

            # 无论是否禁用，都确保导入文件中的SKU被启用
            cursor.execute(f'''
                UPDATE materials SET is_disabled = 0
                WHERE sku IN ({placeholders})
            ''', list(import_skus))

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
                current_qty = material['quantity']

                # 一致性校验：当前库存须与预览时一致，否则提示重新预览
                if item.current_quantity is not None and current_qty != item.current_quantity:
                    return ExcelImportResponse(
                        success=False,
                        in_count=in_count,
                        out_count=out_count,
                        new_count=new_count,
                        records_created=records_created,
                        message=f"库存已变化，SKU {item.sku} 当前库存 {current_qty} 与预览值 {item.current_quantity} 不一致，请重新预览后再导入。"
                    )

                # 无论是否有库存变动，都更新基本信息（安全库存、分类、单位、位置）
                # 注意：这里我们信任导入文件中的信息为最新
                cursor.execute('''
                    UPDATE materials 
                    SET safe_stock = ?, category = ?, unit = ?, location = ?
                    WHERE id = ?
                ''', (
                    item.safe_stock if item.safe_stock is not None else 20,
                    item.category or '未分类',
                    item.unit or '个',
                    item.location or '',
                    material_id
                ))

                if item.operation == 'none':
                    continue

                abs_diff = abs(item.difference)

                if item.operation == 'in':
                    # 入库
                    new_qty = current_qty + abs_diff
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
                    # 出库（不允许负库存）
                    if current_qty - abs_diff < 0:
                        return ExcelImportResponse(
                            success=False,
                            in_count=in_count,
                            out_count=out_count,
                            new_count=new_count,
                            records_created=records_created,
                            message=f"出库失败：SKU {item.sku} 出库 {abs_diff} 超过当前库存 {current_qty}，已终止导入。"
                        )

                    new_qty = current_qty - abs_diff
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

    warning_text = f" {' '.join(warnings)}" if warnings else ""
    return ExcelImportResponse(
        success=True,
        in_count=in_count,
        out_count=out_count,
        new_count=new_count,
        records_created=records_created,
        message=f'导入完成：{in_count}条入库，{out_count}条出库，{new_count}条新增物料。{warning_text}'.strip()
    )


@app.get("/api/inventory/export-excel")
def export_inventory_records(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    product_name: Optional[str] = Query(None, description="产品名称"),
    record_type: Optional[str] = Query(None, description="记录类型(in/out)")
):
    """导出出入库记录为Excel（支持筛选）"""
    with get_db() as conn:
        cursor = conn.cursor()

        query = '''
            SELECT m.name, m.sku, m.category, r.type, r.quantity, r.operator, r.reason, r.created_at
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
            query += ' AND m.name LIKE ?'
            params.append(f'%{product_name}%')
        if record_type and record_type != 'all':
            query += ' AND r.type = ?'
            params.append(record_type)

        query += ' ORDER BY r.created_at DESC'
        cursor.execute(query, params)
        records = cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "出入库记录"

    headers = ['物料名称', '物料编码', '商品类型', '记录类型', '数量', '操作人', '原因', '时间']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)

    for row_idx, record in enumerate(records, 2):
        ws.cell(row=row_idx, column=1, value=record['name'])
        ws.cell(row=row_idx, column=2, value=record['sku'])
        ws.cell(row=row_idx, column=3, value=record['category'])
        ws.cell(row=row_idx, column=4, value='入库' if record['type'] == 'in' else '出库')
        ws.cell(row=row_idx, column=5, value=record['quantity'])
        ws.cell(row=row_idx, column=6, value=record['operator'])
        ws.cell(row=row_idx, column=7, value=record['reason'])
        ws.cell(row=row_idx, column=8, value=record['created_at'])

    # 设置列宽
    column_widths = [22, 18, 14, 12, 10, 14, 24, 22]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width

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
