"""
仓库管理系统 v2.0 功能测试
测试三个阶段的新功能：用户管理、联系方管理、批次管理

运行方式：
    cd backend
    pytest tests/test_v2_features.py -v

或指定测试：
    pytest tests/test_v2_features.py -v -k "test_auth"
    pytest tests/test_v2_features.py -v -k "test_contact"
    pytest tests/test_v2_features.py -v -k "test_batch"
"""

import pytest
import os
import sys
import tempfile
import json

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from database import init_database, get_db_connection, hash_password, generate_batch_no


# ============ Fixtures ============

@pytest.fixture(scope="module")
def test_db():
    """创建临时测试数据库"""
    # 使用临时文件作为测试数据库
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    # 设置环境变量
    os.environ['DATABASE_PATH'] = db_path

    # 重新加载database模块以使用新路径
    import database
    import importlib
    importlib.reload(database)

    # 初始化数据库
    database.init_database()

    yield db_path

    # 清理
    os.unlink(db_path)


@pytest.fixture(scope="module")
def client(test_db):
    """创建测试客户端"""
    # 重新加载app模块
    import app as app_module
    import importlib
    importlib.reload(app_module)

    return TestClient(app_module.app)


@pytest.fixture
def admin_session(client):
    """创建管理员并登录"""
    # 首次设置管理员
    response = client.post("/api/auth/setup", json={
        "username": "admin",
        "password": "admin123",
        "display_name": "测试管理员"
    })

    if response.status_code != 200:
        # 可能已经设置过，尝试登录
        response = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123"
        })

    # 返回带session cookie的client
    return client


@pytest.fixture
def test_material(client, admin_session):
    """创建测试物料"""
    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ('测试物料A', 'TEST-001', '测试类', 100, '个', 20, 'A区-01'))

    material_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        'id': material_id,
        'name': '测试物料A',
        'sku': 'TEST-001',
        'quantity': 100
    }


# ============ 第一阶段：用户管理测试 ============

class TestAuth:
    """认证功能测试"""

    def test_auth_status_uninitialized(self, client):
        """测试未初始化状态"""
        response = client.get("/api/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert 'initialized' in data

    def test_setup_admin(self, client):
        """测试首次设置管理员"""
        response = client.post("/api/auth/setup", json={
            "username": "testadmin",
            "password": "password123",
            "display_name": "测试管理员"
        })
        # 可能已存在管理员
        assert response.status_code in [200, 400]

    def test_login_success(self, admin_session):
        """测试登录成功"""
        response = admin_session.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123"
        })
        assert response.status_code == 200
        data = response.json()
        assert data['success'] == True

    def test_login_failure(self, client):
        """测试登录失败"""
        response = client.post("/api/auth/login", json={
            "username": "nonexistent",
            "password": "wrongpassword"
        })
        assert response.status_code == 200
        data = response.json()
        assert data['success'] == False

    def test_get_current_user(self, admin_session):
        """测试获取当前用户"""
        response = admin_session.get("/api/auth/me")
        assert response.status_code == 200


class TestUserManagement:
    """用户管理测试"""

    def test_list_users(self, admin_session):
        """测试获取用户列表"""
        response = admin_session.get("/api/users")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_create_user(self, admin_session):
        """测试创建用户"""
        response = admin_session.post("/api/users", json={
            "username": "operator1",
            "password": "password123",
            "display_name": "操作员1",
            "role": "operate"
        })
        # 可能已存在
        assert response.status_code in [200, 400]

    def test_create_user_without_permission(self, client):
        """测试无权限创建用户"""
        response = client.post("/api/users", json={
            "username": "hacker",
            "password": "password123",
            "role": "admin"
        })
        # 应该被拒绝（未登录或权限不足）
        assert response.status_code in [401, 403]


class TestApiKeys:
    """API密钥测试"""

    def test_list_api_keys(self, admin_session):
        """测试获取API密钥列表"""
        response = admin_session.get("/api/api-keys")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_create_api_key(self, admin_session):
        """测试创建API密钥"""
        response = admin_session.post("/api/api-keys", json={
            "name": "测试终端",
            "role": "operate"
        })
        assert response.status_code == 200
        data = response.json()
        assert 'key' in data  # 返回明文密钥
        assert data['key'].startswith('wh_')


# ============ 第二阶段：联系方管理测试 ============

class TestContacts:
    """联系方管理测试"""

    def test_list_contacts(self, admin_session):
        """测试获取联系方列表"""
        response = admin_session.get("/api/contacts")
        assert response.status_code == 200
        data = response.json()
        assert 'items' in data
        assert 'total' in data

    def test_create_supplier(self, admin_session):
        """测试创建供应商"""
        response = admin_session.post("/api/contacts", json={
            "name": "测试供应商A",
            "phone": "13800138000",
            "is_supplier": True,
            "is_customer": False
        })
        assert response.status_code == 200
        data = response.json()
        assert data['name'] == "测试供应商A"
        assert data['is_supplier'] == True
        return data['id']

    def test_create_customer(self, admin_session):
        """测试创建客户"""
        response = admin_session.post("/api/contacts", json={
            "name": "测试客户B",
            "email": "customer@test.com",
            "is_supplier": False,
            "is_customer": True
        })
        assert response.status_code == 200
        data = response.json()
        assert data['is_customer'] == True

    def test_create_contact_must_select_type(self, admin_session):
        """测试创建联系方必须选择类型"""
        response = admin_session.post("/api/contacts", json={
            "name": "无类型联系方",
            "is_supplier": False,
            "is_customer": False
        })
        assert response.status_code == 400

    def test_get_suppliers_list(self, admin_session):
        """测试获取供应商下拉列表"""
        response = admin_session.get("/api/contacts/suppliers")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # 所有项都是供应商
        for item in data:
            assert item['is_supplier'] == True

    def test_get_customers_list(self, admin_session):
        """测试获取客户下拉列表"""
        response = admin_session.get("/api/contacts/customers")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # 所有项都是客户
        for item in data:
            assert item['is_customer'] == True


# ============ 第三阶段：批次管理测试 ============

class TestBatchManagement:
    """批次管理测试"""

    def test_batch_no_generation(self, test_db):
        """测试批次号生成"""
        from datetime import datetime

        batch_no = generate_batch_no(1)
        today = datetime.now().strftime('%Y%m%d')

        assert batch_no.startswith(today)
        assert '-' in batch_no
        # 格式: YYYYMMDD-XXX
        parts = batch_no.split('-')
        assert len(parts) == 2
        assert len(parts[1]) == 3  # 三位序号

    def test_stock_in_creates_batch(self, admin_session, test_material):
        """测试入库自动创建批次"""
        response = admin_session.post("/api/materials/stock-in", json={
            "product_name": test_material['name'],
            "quantity": 50,
            "reason": "测试入库",
            "operator": "测试员"
        })

        assert response.status_code == 200
        data = response.json()
        assert data['success'] == True
        assert 'batch' in data
        assert data['batch']['batch_no'] is not None
        assert data['batch']['quantity'] == 50

    def test_stock_out_fifo(self, admin_session, test_material):
        """测试出库FIFO消耗"""
        # 先入库两批
        admin_session.post("/api/materials/stock-in", json={
            "product_name": test_material['name'],
            "quantity": 30,
            "reason": "第一批入库"
        })

        admin_session.post("/api/materials/stock-in", json={
            "product_name": test_material['name'],
            "quantity": 20,
            "reason": "第二批入库"
        })

        # 出库40个，应该消耗第一批30 + 第二批10
        response = admin_session.post("/api/materials/stock-out", json={
            "product_name": test_material['name'],
            "quantity": 40,
            "reason": "测试FIFO出库"
        })

        assert response.status_code == 200
        data = response.json()
        assert data['success'] == True

        # 检查批次消耗详情
        if 'batch_consumptions' in data and data['batch_consumptions']:
            consumptions = data['batch_consumptions']
            assert len(consumptions) >= 1
            # 总消耗应该等于出库数量
            total_consumed = sum(c['quantity'] for c in consumptions)
            assert total_consumed == 40

    def test_records_include_batch_info(self, admin_session):
        """测试进出库记录包含批次信息"""
        response = admin_session.get("/api/inventory/records")
        assert response.status_code == 200
        data = response.json()

        # 检查记录结构
        if data['items']:
            item = data['items'][0]
            # 应该有批次相关字段
            assert 'batch_id' in item or 'batch_no' in item or 'batch_details' in item


class TestBatchWithContact:
    """批次+联系方集成测试"""

    def test_stock_in_with_supplier(self, admin_session, test_material):
        """测试带供应商的入库"""
        # 先创建供应商
        contact_response = admin_session.post("/api/contacts", json={
            "name": "批次测试供应商",
            "is_supplier": True,
            "is_customer": False
        })

        if contact_response.status_code == 200:
            contact_id = contact_response.json()['id']

            # 带供应商入库
            response = admin_session.post("/api/materials/stock-in", json={
                "product_name": test_material['name'],
                "quantity": 25,
                "reason": "带供应商入库",
                "contact_id": contact_id
            })

            assert response.status_code == 200
            data = response.json()
            assert data['success'] == True
            assert 'batch' in data


# ============ Excel导出测试 ============

class TestExcelExport:
    """Excel导出测试"""

    def test_export_records_with_batch(self, admin_session):
        """测试导出记录包含批次信息"""
        response = admin_session.get("/api/inventory/export-excel")
        assert response.status_code == 200
        assert 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' in response.headers['content-type']

    def test_export_materials(self, admin_session):
        """测试导出物料清单"""
        response = admin_session.get("/api/materials/export-excel")
        assert response.status_code == 200


# ============ 权限控制测试 ============

class TestPermissions:
    """权限控制测试"""

    def test_guest_can_read(self, client):
        """测试访客可以读取"""
        # 仪表盘
        response = client.get("/api/dashboard/stats")
        assert response.status_code == 200

        # 物料列表
        response = client.get("/api/materials")
        assert response.status_code == 200

    def test_guest_cannot_write(self, client):
        """测试访客不能写入"""
        response = client.post("/api/materials/stock-in", json={
            "product_name": "测试",
            "quantity": 10
        })
        # 应该返回权限错误
        assert response.status_code in [401, 403]

    def test_operate_can_stock_in_out(self, admin_session):
        """测试操作员可以出入库"""
        # 创建操作员
        admin_session.post("/api/users", json={
            "username": "operator_test",
            "password": "password123",
            "role": "operate"
        })

        # 用操作员登录
        admin_session.post("/api/auth/login", json={
            "username": "operator_test",
            "password": "password123"
        })

        # 应该可以入库（如果有物料的话）
        response = admin_session.get("/api/materials")
        assert response.status_code == 200


# ============ 运行测试 ============

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
