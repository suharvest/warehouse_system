"""
Regression safety net for /api/inventory/records (backend/app.py:3363).

Pins down the *current* behavior so the upcoming SQLAlchemy/MySQL migration
can prove zero-regression. Filters covered: record_type, start_date,
end_date, contact_id, operator_user_id, reason_category, sort_order, and
multi-tenant isolation.
"""
import uuid
from datetime import datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def _reset_admin_tenant(test_db):
    """Re-pin admin to tenant_id=1 around every test, defensively."""
    import os as _os
    _os.environ['DATABASE_PATH'] = test_db
    import database as _database
    _database.DATABASE_PATH = test_db

    def _reset():
        try:
            conn = _database.get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET tenant_id = 1 WHERE username = 'admin'")
            conn.commit()
            conn.close()
        except Exception:
            pass

    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_record(*, material_id, type_, quantity, warehouse_id, tenant_id,
                 created_at=None, contact_id=None, operator='sys',
                 operator_user_id=None, reason_category='purchase',
                 reason_note=None, actual_operator=None):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO inventory_records "
        "(material_id, type, quantity, operator, operator_user_id, "
        " actual_operator, reason_category, reason_note, contact_id, "
        " warehouse_id, tenant_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (material_id, type_, quantity, operator, operator_user_id,
         actual_operator, reason_category, reason_note, contact_id,
         warehouse_id, tenant_id,
         created_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def _seed_material(name, sku, *, warehouse_id, tenant_id, quantity=10):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, "
        "safe_stock, location, warehouse_id, tenant_id) "
        "VALUES (?, ?, 'T', ?, 'pcs', 1, '', ?, ?)",
        (name, sku, quantity, warehouse_id, tenant_id))
    mid = cur.lastrowid
    conn.commit()
    conn.close()
    return mid


# ---------------------------------------------------------------------------
# 1. Basic shape
# ---------------------------------------------------------------------------

def test_records_pagination_shape(admin_client, default_warehouse_id,
                                  sample_material):
    admin_client.post("/api/materials/stock-in", json={
        "product_name": sample_material['name'], "quantity": 1,
        "reason_category": "purchase",
        "warehouse_id": default_warehouse_id,
    })
    resp = admin_client.get("/api/inventory/records",
                            params={"page": 1, "page_size": 10})
    assert resp.status_code == 200
    data = resp.json()
    for k in ("page", "page_size", "total", "total_pages", "items"):
        assert k in data, f"missing key {k}"
    assert isinstance(data['items'], list)
    assert data['page'] == 1
    assert data['page_size'] == 10


# ---------------------------------------------------------------------------
# 1b. actual_operator passthrough（人脸识别姓名快照）
# ---------------------------------------------------------------------------

def test_records_returns_actual_operator(admin_client,
                                            default_warehouse_id):
    name = f"FaceMat-{uuid.uuid4().hex[:6]}"
    sku = f"FC-{uuid.uuid4().hex[:6]}"
    mid = _seed_material(name, sku, warehouse_id=default_warehouse_id,
                         tenant_id=1)

    _seed_record(material_id=mid, type_='in', quantity=1,
                 warehouse_id=default_warehouse_id, tenant_id=1,
                 operator='seeed', actual_operator='张三')
    _seed_record(material_id=mid, type_='in', quantity=2,
                 warehouse_id=default_warehouse_id, tenant_id=1,
                 operator='seeed')

    resp = admin_client.get("/api/inventory/records", params={
        "page_size": 100, "product_name": name, "sort_order": "asc",
    })
    assert resp.status_code == 200
    items = resp.json()['items']
    assert len(items) == 2
    assert items[0]['actual_operator'] == '张三'
    assert items[1]['actual_operator'] is None


def test_manual_add_record_stores_actual_operator(admin_client,
                                                  default_warehouse_id):
    """手工 add-record 表单填写 actual_operator，出入库均落库并由列表 API 返回。"""
    name = f"ManualMat-{uuid.uuid4().hex[:6]}"
    sku = f"MN-{uuid.uuid4().hex[:6]}"
    _seed_material(name, sku, warehouse_id=default_warehouse_id, tenant_id=1)

    # 手工入库，填写实际操作人
    resp = admin_client.post("/api/inventory/add-record", json={
        "product_name": name,
        "type": "in",
        "quantity": 10,
        "reason_category": "purchase",
        "actual_operator": "王五",
        "warehouse_id": default_warehouse_id,
    })
    assert resp.status_code == 200 and resp.json()['success'] is True

    # 手工出库，填写不同的实际操作人
    resp = admin_client.post("/api/inventory/add-record", json={
        "product_name": name,
        "type": "out",
        "quantity": 3,
        "reason_category": "sell",
        "actual_operator": "赵六",
        "warehouse_id": default_warehouse_id,
    })
    assert resp.status_code == 200 and resp.json()['success'] is True

    resp = admin_client.get("/api/inventory/records", params={
        "page_size": 100, "product_name": name, "sort_order": "asc",
    })
    assert resp.status_code == 200
    items = resp.json()['items']
    by_type = {it['type']: it for it in items}
    assert by_type['in']['actual_operator'] == '王五'
    assert by_type['out']['actual_operator'] == '赵六'


def test_out_record_variant_from_consumed_batch(admin_client, default_warehouse_id):
    """出库记录 batch_id 为 NULL，规格须从被消耗批次带出（回归：原来显示 '-'）。"""
    name = f"VarMat-{uuid.uuid4().hex[:6]}"
    sku = f"VR-{uuid.uuid4().hex[:6]}"
    _seed_material(name, sku, warehouse_id=default_warehouse_id, tenant_id=1)

    resp = admin_client.post("/api/materials/stock-in", json={
        "product_name": name, "quantity": 6, "reason_category": "purchase",
        "warehouse_id": default_warehouse_id, "variant": "红",
    })
    assert resp.status_code == 200 and resp.json()['success'] is True
    resp = admin_client.post("/api/materials/stock-out", json={
        "product_name": name, "quantity": 6, "reason_category": "sell",
        "warehouse_id": default_warehouse_id, "variant": "红",
    })
    assert resp.status_code == 200 and resp.json()['success'] is True

    resp = admin_client.get("/api/inventory/records", params={
        "page_size": 100, "product_name": name, "sort_order": "asc",
    })
    assert resp.status_code == 200
    items = resp.json()['items']
    out_items = [it for it in items if it['type'] == 'out']
    assert out_items
    assert all(it['variant'] == '红' for it in out_items), out_items


# ---------------------------------------------------------------------------
# 2. Filter by record_type
# ---------------------------------------------------------------------------

def test_records_filter_by_record_type(admin_client, default_warehouse_id,
                                       sample_material):
    # one in, one out
    admin_client.post("/api/materials/stock-in", json={
        "product_name": sample_material['name'], "quantity": 5,
        "reason_category": "purchase",
        "warehouse_id": default_warehouse_id,
    })
    admin_client.post("/api/materials/stock-out", json={
        "product_name": sample_material['name'], "quantity": 1,
        "reason_category": "sell",
        "warehouse_id": default_warehouse_id,
    })

    resp_in = admin_client.get("/api/inventory/records",
                               params={"record_type": "in", "page_size": 100})
    assert resp_in.status_code == 200
    for item in resp_in.json()['items']:
        assert item['type'] == 'in'

    resp_out = admin_client.get("/api/inventory/records",
                                params={"record_type": "out",
                                        "page_size": 100})
    assert resp_out.status_code == 200
    for item in resp_out.json()['items']:
        assert item['type'] == 'out'


# ---------------------------------------------------------------------------
# 3. Date filter boundaries (DATE(created_at) >= ? AND DATE(created_at) <= ?)
#    Both inclusive — pinning down current behavior.
# ---------------------------------------------------------------------------

def test_records_date_filters_are_inclusive(admin_client,
                                            default_warehouse_id):
    # Seed records on three distinct dates
    name = f"DateMat-{uuid.uuid4().hex[:6]}"
    sku = f"DT-{uuid.uuid4().hex[:6]}"
    mid = _seed_material(name, sku, warehouse_id=default_warehouse_id,
                         tenant_id=1)

    today = datetime.now().date()
    d_minus_2 = (datetime.now() - timedelta(days=2))
    d_minus_1 = (datetime.now() - timedelta(days=1))
    d_today = datetime.now()

    for ts in (d_minus_2, d_minus_1, d_today):
        _seed_record(material_id=mid, type_='in', quantity=1,
                     warehouse_id=default_warehouse_id, tenant_id=1,
                     created_at=ts.strftime('%Y-%m-%d %H:%M:%S'))

    # Filter to just yesterday (start=end=yesterday) → 1 record
    yday = d_minus_1.strftime('%Y-%m-%d')
    resp = admin_client.get("/api/inventory/records", params={
        "start_date": yday, "end_date": yday,
        "page_size": 100, "product_name": name,
    })
    assert resp.status_code == 200
    items = resp.json()['items']
    assert len(items) == 1, items

    # Filter from yesterday to today → 2 records
    resp = admin_client.get("/api/inventory/records", params={
        "start_date": yday,
        "end_date": today.strftime('%Y-%m-%d'),
        "page_size": 100, "product_name": name,
    })
    items = resp.json()['items']
    assert len(items) == 2, items


# ---------------------------------------------------------------------------
# 4. contact_id / operator_user_id / reason_category filters
# ---------------------------------------------------------------------------

def test_records_filter_by_contact_id(admin_client, default_warehouse_id):
    from database import get_db_connection
    name = f"CMat-{uuid.uuid4().hex[:6]}"
    sku = f"CM-{uuid.uuid4().hex[:6]}"
    mid = _seed_material(name, sku, warehouse_id=default_warehouse_id,
                         tenant_id=1)

    # Create two contacts (use CURRENT_TIMESTAMP for portability)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO contacts (name, is_supplier, is_customer, tenant_id, "
        " created_at) VALUES ('CA', 1, 0, 1, CURRENT_TIMESTAMP)")
    c1 = cur.lastrowid
    cur.execute(
        "INSERT INTO contacts (name, is_supplier, is_customer, tenant_id, "
        " created_at) VALUES ('CB', 1, 0, 1, CURRENT_TIMESTAMP)")
    c2 = cur.lastrowid
    conn.commit()
    conn.close()

    _seed_record(material_id=mid, type_='in', quantity=1,
                 warehouse_id=default_warehouse_id, tenant_id=1, contact_id=c1)
    _seed_record(material_id=mid, type_='in', quantity=1,
                 warehouse_id=default_warehouse_id, tenant_id=1, contact_id=c2)

    resp = admin_client.get("/api/inventory/records", params={
        "contact_id": c1, "page_size": 100, "product_name": name,
    })
    assert resp.status_code == 200
    items = resp.json()['items']
    assert len(items) == 1
    assert items[0]['contact_id'] == c1


def test_records_filter_by_reason_category(admin_client,
                                           default_warehouse_id):
    name = f"RMat-{uuid.uuid4().hex[:6]}"
    sku = f"RM-{uuid.uuid4().hex[:6]}"
    mid = _seed_material(name, sku, warehouse_id=default_warehouse_id,
                         tenant_id=1)

    _seed_record(material_id=mid, type_='in', quantity=1,
                 warehouse_id=default_warehouse_id, tenant_id=1,
                 reason_category='purchase')
    _seed_record(material_id=mid, type_='in', quantity=1,
                 warehouse_id=default_warehouse_id, tenant_id=1,
                 reason_category='return')

    resp = admin_client.get("/api/inventory/records", params={
        "reason_category": "return",
        "page_size": 100, "product_name": name,
    })
    items = resp.json()['items']
    assert len(items) == 1
    assert items[0]['reason_category'] == 'return'


def test_records_filter_by_operator_user_id(admin_client,
                                            default_warehouse_id):
    name = f"OMat-{uuid.uuid4().hex[:6]}"
    sku = f"OM-{uuid.uuid4().hex[:6]}"
    mid = _seed_material(name, sku, warehouse_id=default_warehouse_id,
                         tenant_id=1)

    # Seed real users (FK constraint on operator_user_id is enforced by MySQL,
    # silently ignored by sqlite). Use INSERT ... ON DUPLICATE-KEY-friendly
    # pattern via a SELECT-then-INSERT helper.
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    for uid, uname in ((999, 'op-999'), (1000, 'op-1000')):
        cur.execute("SELECT 1 FROM users WHERE id = ?", (uid,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (id, username, password_hash, role, tenant_id) "
                "VALUES (?, ?, 'x', 'operate', 1)",
                (uid, uname))
    conn.commit()
    conn.close()

    _seed_record(material_id=mid, type_='in', quantity=1,
                 warehouse_id=default_warehouse_id, tenant_id=1,
                 operator_user_id=999)
    _seed_record(material_id=mid, type_='in', quantity=1,
                 warehouse_id=default_warehouse_id, tenant_id=1,
                 operator_user_id=1000)

    resp = admin_client.get("/api/inventory/records", params={
        "operator_user_id": 999, "page_size": 100, "product_name": name,
    })
    items = resp.json()['items']
    assert len(items) == 1
    assert items[0]['operator_user_id'] == 999


# ---------------------------------------------------------------------------
# 5. Sort order
# ---------------------------------------------------------------------------

def test_records_sort_order_desc_then_asc(admin_client,
                                          default_warehouse_id):
    name = f"SortMat-{uuid.uuid4().hex[:6]}"
    sku = f"ST-{uuid.uuid4().hex[:6]}"
    mid = _seed_material(name, sku, warehouse_id=default_warehouse_id,
                         tenant_id=1)

    base = datetime.now() - timedelta(hours=3)
    for i in range(3):
        ts = base + timedelta(hours=i)
        _seed_record(material_id=mid, type_='in', quantity=i + 1,
                     warehouse_id=default_warehouse_id, tenant_id=1,
                     created_at=ts.strftime('%Y-%m-%d %H:%M:%S'))

    desc = admin_client.get("/api/inventory/records", params={
        "product_name": name, "sort_order": "desc", "page_size": 100,
    }).json()['items']
    asc = admin_client.get("/api/inventory/records", params={
        "product_name": name, "sort_order": "asc", "page_size": 100,
    }).json()['items']

    assert len(desc) == 3 and len(asc) == 3
    assert [r['id'] for r in desc] == list(reversed([r['id'] for r in asc]))


# ---------------------------------------------------------------------------
# 6. Multi-tenant isolation (THE main thing for the SA migration)
# ---------------------------------------------------------------------------

def test_records_multi_tenant_isolation(admin_client, app_instance,
                                        monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")

    # Promote admin to global, build two tenants
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()

    suffix = uuid.uuid4().hex[:6]
    t_a = admin_client.post("/api/tenants", json={
        "slug": f"ta-{suffix}", "name": f"TA{suffix}"}).json()['id']
    t_b = admin_client.post("/api/tenants", json={
        "slug": f"tb-{suffix}", "name": f"TB{suffix}"}).json()['id']
    wh_a = admin_client.post("/api/warehouses", json={
        "slug": f"wa-{suffix}", "name": f"WA{suffix}",
        "tenant_id": t_a}).json()['id']
    wh_b = admin_client.post("/api/warehouses", json={
        "slug": f"wb-{suffix}", "name": f"WB{suffix}",
        "tenant_id": t_b}).json()['id']

    name_a = f"MA-{suffix}"
    name_b = f"MB-{suffix}"
    mid_a = _seed_material(name_a, f"sa-{suffix}",
                           warehouse_id=wh_a, tenant_id=t_a)
    mid_b = _seed_material(name_b, f"sb-{suffix}",
                           warehouse_id=wh_b, tenant_id=t_b)

    # 5 records for tenant A, 3 records for tenant B
    for _ in range(5):
        _seed_record(material_id=mid_a, type_='in', quantity=1,
                     warehouse_id=wh_a, tenant_id=t_a)
    for _ in range(3):
        _seed_record(material_id=mid_b, type_='in', quantity=1,
                     warehouse_id=wh_b, tenant_id=t_b)

    # Create a tenant A admin user and login as them
    admin_client.post("/api/users", json={
        "username": f"ua-{suffix}", "password": "Pass123!",
        "display_name": f"UA{suffix}", "role": 'admin', "tenant_id": t_a,
    })

    from fastapi.testclient import TestClient
    c = TestClient(app_instance)
    r = c.post("/api/auth/login", json={
        "username": f"ua-{suffix}", "password": "Pass123!"})
    assert r.status_code == 200, r.text

    resp = c.get("/api/inventory/records", params={"page_size": 100})
    assert resp.status_code == 200
    items = resp.json()['items']
    # Every returned record must have material_name name_a, never name_b
    for item in items:
        assert item['material_name'] != name_b, (
            f"tenant A saw tenant B record: {item}")
    # We seeded 5 records of name_a; nothing else for that material.
    a_count = sum(1 for it in items if it['material_name'] == name_a)
    assert a_count == 5, f"expected 5 tenant A records, got {a_count}"


# ---------------------------------------------------------------------------
# 7. API key with warehouse_id filter sees only that warehouse
# ---------------------------------------------------------------------------

def test_records_api_key_warehouse_scope(admin_client, app_instance,
                                          default_warehouse_id):
    # Build a second warehouse in same tenant
    suffix = uuid.uuid4().hex[:6]
    wh2 = admin_client.post("/api/warehouses", json={
        "slug": f"wb-{suffix}", "name": f"WB{suffix}"}).json()['id']
    try:
        name = f"WKMat-{suffix}"
        sku = f"WK-{suffix}"
        mid_default = _seed_material(name, sku,
                                     warehouse_id=default_warehouse_id,
                                     tenant_id=1)
        mid_wh2 = _seed_material(name + "-2", sku + "-2",
                                 warehouse_id=wh2, tenant_id=1)

        _seed_record(material_id=mid_default, type_='in', quantity=1,
                     warehouse_id=default_warehouse_id, tenant_id=1)
        _seed_record(material_id=mid_wh2, type_='in', quantity=1,
                     warehouse_id=wh2, tenant_id=1)

        # Key bound to default warehouse
        info = admin_client.post("/api/api-keys", json={
            "name": f"k-{suffix}", "role": "view",
            "warehouse_id": default_warehouse_id,
        }).json()

        from fastapi.testclient import TestClient
        c = TestClient(app_instance)
        resp = c.get("/api/inventory/records",
                     headers={"X-API-Key": info['key']})
        assert resp.status_code == 200
        for item in resp.json()['items']:
            assert item['warehouse_id'] == default_warehouse_id
    finally:
        # Disable the extra warehouse so it doesn't pollute
        # infer_single_writable_warehouse_id for later tests.
        from database import get_db_connection
        cn = get_db_connection()
        cu = cn.cursor()
        cu.execute("UPDATE warehouses SET is_disabled = 1 WHERE id = ?",
                   (wh2,))
        cn.commit()
        cn.close()
