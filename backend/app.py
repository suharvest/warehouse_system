from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timedelta
import sqlite3
from database import init_database, generate_mock_data, get_db_connection

app = Flask(__name__)
CORS(app)

# 初始化数据库
init_database()
generate_mock_data()

@app.route('/api/dashboard/stats', methods=['GET'])
def get_dashboard_stats():
    """获取仪表盘统计数据"""
    conn = get_db_connection()
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

    conn.close()

    return jsonify({
        'total_stock': total_stock,
        'today_in': today_in,
        'today_out': today_out,
        'low_stock_count': low_stock_count,
        'material_types': material_types,
        'in_change': in_change,
        'out_change': out_change
    })

@app.route('/api/dashboard/category-distribution', methods=['GET'])
def get_category_distribution():
    """获取库存类型分布"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT category, SUM(quantity) as total
        FROM materials
        GROUP BY category
        ORDER BY total DESC
    ''')

    data = []
    for row in cursor.fetchall():
        data.append({
            'name': row['category'],
            'value': row['total']
        })

    conn.close()
    return jsonify(data)

@app.route('/api/dashboard/weekly-trend', methods=['GET'])
def get_weekly_trend():
    """获取近7天出入库趋势"""
    conn = get_db_connection()
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

    conn.close()

    return jsonify({
        'dates': dates,
        'in_data': in_data,
        'out_data': out_data
    })

@app.route('/api/dashboard/top-stock', methods=['GET'])
def get_top_stock():
    """获取库存TOP10"""
    conn = get_db_connection()
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

    conn.close()

    return jsonify({
        'names': names,
        'quantities': quantities,
        'categories': categories
    })

@app.route('/api/dashboard/low-stock-alert', methods=['GET'])
def get_low_stock_alert():
    """获取库存预警列表"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT name, sku, category, quantity, safe_stock, location
        FROM materials
        WHERE quantity < safe_stock
        ORDER BY (quantity - safe_stock) ASC
        LIMIT 20
    ''')

    data = []
    for row in cursor.fetchall():
        data.append({
            'name': row['name'],
            'sku': row['sku'],
            'category': row['category'],
            'quantity': row['quantity'],
            'safe_stock': row['safe_stock'],
            'location': row['location'],
            'shortage': row['safe_stock'] - row['quantity']
        })

    conn.close()
    return jsonify(data)

@app.route('/api/materials/xiaozhi', methods=['GET'])
def get_xiaozhi_stock():
    """获取 watcher-xiaozhi 相关库存"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT name, sku, quantity, unit, category, location
        FROM materials
        WHERE name LIKE '%xiaozhi%' OR name LIKE '%watcher%'
        ORDER BY quantity DESC
    ''')

    data = []
    for row in cursor.fetchall():
        data.append({
            'name': row['name'],
            'sku': row['sku'],
            'quantity': row['quantity'],
            'unit': row['unit'],
            'category': row['category'],
            'location': row['location']
        })

    conn.close()
    return jsonify(data)

@app.route('/api/materials/all', methods=['GET'])
def get_all_materials():
    """获取所有库存"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT name, sku, category, quantity, unit, safe_stock, location
        FROM materials
        ORDER BY name ASC
    ''')

    data = []
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

        data.append({
            'name': row['name'],
            'sku': row['sku'],
            'category': row['category'],
            'quantity': quantity,
            'unit': row['unit'],
            'safe_stock': safe_stock,
            'location': row['location'],
            'status': status,
            'status_text': status_text
        })

    conn.close()
    return jsonify(data)


@app.route('/api/materials/product-stats', methods=['GET'])
def get_product_stats():
    """获取单个产品的统计数据"""
    product_name = request.args.get('name', '')

    if not product_name:
        return jsonify({'error': '缺少产品名称参数'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # 查询产品基本信息
    cursor.execute('''
        SELECT id, name, sku, quantity, unit, safe_stock, location
        FROM materials
        WHERE name = ?
    ''', (product_name,))

    product = cursor.fetchone()
    if not product:
        conn.close()
        return jsonify({'error': '产品不存在'}), 404

    material_id = product['id']
    current_stock = product['quantity']
    unit = product['unit']
    safe_stock = product['safe_stock']

    # 获取今天的日期
    from datetime import datetime, timedelta
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

    conn.close()

    # 计算变化百分比
    in_change = ((today_in - yesterday_in) / yesterday_in * 100) if yesterday_in > 0 else 0
    out_change = ((today_out - yesterday_out) / yesterday_out * 100) if yesterday_out > 0 else 0

    return jsonify({
        'name': product_name,
        'sku': product['sku'],
        'current_stock': current_stock,
        'unit': unit,
        'safe_stock': safe_stock,
        'location': product['location'],
        'today_in': today_in,
        'today_out': today_out,
        'in_change': round(in_change, 1),
        'out_change': round(out_change, 1),
        'total_in': total_in,
        'total_out': total_out
    })


@app.route('/api/materials/product-trend', methods=['GET'])
def get_product_trend():
    """获取单个产品的近7天趋势"""
    product_name = request.args.get('name', '')

    if not product_name:
        return jsonify({'error': '缺少产品名称参数'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # 查询产品ID
    cursor.execute('SELECT id FROM materials WHERE name = ?', (product_name,))
    product = cursor.fetchone()
    if not product:
        conn.close()
        return jsonify({'error': '产品不存在'}), 404

    material_id = product['id']

    # 获取近7天的日期
    from datetime import datetime, timedelta
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

    conn.close()

    return jsonify({
        'dates': dates,
        'in_data': in_data,
        'out_data': out_data
    })


@app.route('/api/materials/product-records', methods=['GET'])
def get_product_records():
    """获取单个产品的出入库记录"""
    product_name = request.args.get('name', '')

    if not product_name:
        return jsonify({'error': '缺少产品名称参数'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # 查询产品ID
    cursor.execute('SELECT id FROM materials WHERE name = ?', (product_name,))
    product = cursor.fetchone()
    if not product:
        conn.close()
        return jsonify({'error': '产品不存在'}), 404

    material_id = product['id']

    # 查询最近30条记录
    cursor.execute('''
        SELECT type, quantity, operator, reason, created_at
        FROM inventory_records
        WHERE material_id = ?
        ORDER BY created_at DESC
        LIMIT 30
    ''', (material_id,))

    records = []
    for row in cursor.fetchall():
        records.append({
            'type': row['type'],
            'quantity': row['quantity'],
            'operator': row['operator'],
            'reason': row['reason'],
            'created_at': row['created_at']
        })

    conn.close()
    return jsonify(records)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=2124, debug=False)
