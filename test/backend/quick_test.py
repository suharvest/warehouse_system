#!/usr/bin/env python3
"""
快速功能验证脚本
无需pytest，直接运行即可验证核心功能

使用方式：
    1. 启动后端: cd backend && python app.py
    2. 运行测试: python test/backend/quick_test.py

或者一键运行（后台启动服务器）:
    python test/backend/quick_test.py --auto-server
"""

import requests
import json
import sys
import time
import subprocess
import signal
import os

# 配置
PORT = os.environ.get('PORT', '8000')
BASE_URL = os.environ.get('API_URL', f"http://localhost:{PORT}")
TIMEOUT = 5

# 颜色输出
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def log_pass(msg):
    print(f"  {Colors.GREEN}✓{Colors.RESET} {msg}")

def log_fail(msg):
    print(f"  {Colors.RED}✗{Colors.RESET} {msg}")

def log_section(msg):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*50}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{msg}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*50}{Colors.RESET}")

def log_subsection(msg):
    print(f"\n{Colors.YELLOW}>>> {msg}{Colors.RESET}")


class WarehouseAPITest:
    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()
        self.admin_token = None
        self.test_results = {"passed": 0, "failed": 0}

    def check(self, condition, msg):
        """检查条件并记录结果"""
        if condition:
            log_pass(msg)
            self.test_results["passed"] += 1
            return True
        else:
            log_fail(msg)
            self.test_results["failed"] += 1
            return False

    def get(self, endpoint, **kwargs):
        return self.session.get(f"{self.base_url}{endpoint}", timeout=TIMEOUT, **kwargs)

    def post(self, endpoint, data=None, **kwargs):
        return self.session.post(f"{self.base_url}{endpoint}", json=data, timeout=TIMEOUT, **kwargs)

    def put(self, endpoint, data=None, **kwargs):
        return self.session.put(f"{self.base_url}{endpoint}", json=data, timeout=TIMEOUT, **kwargs)

    def delete(self, endpoint, **kwargs):
        return self.session.delete(f"{self.base_url}{endpoint}", timeout=TIMEOUT, **kwargs)

    # ============ 服务器连接测试 ============
    def test_server_connection(self):
        log_section("服务器连接测试")
        try:
            r = self.get("/api/dashboard/stats")
            return self.check(r.status_code == 200, "服务器连接正常")
        except requests.exceptions.ConnectionError:
            log_fail("无法连接到服务器，请确保后端已启动")
            return False

    # ============ 第一阶段：认证测试 ============
    def test_auth(self):
        log_section("第一阶段：用户认证测试")

        # 1. 检查初始化状态
        log_subsection("认证状态")
        r = self.get("/api/auth/status")
        self.check(r.status_code == 200, "获取认证状态")
        status = r.json()
        initialized = status.get('initialized', False)
        print(f"    系统初始化状态: {'已初始化' if initialized else '未初始化'}")

        # 2. 设置管理员（如果未初始化）
        log_subsection("管理员设置")
        if not initialized:
            r = self.post("/api/auth/setup", {
                "username": "admin",
                "password": "admin123",
                "display_name": "测试管理员"
            })
            self.check(r.status_code == 200, "创建管理员账号")
        else:
            log_pass("管理员已存在，跳过创建")

        # 3. 登录测试
        log_subsection("登录测试")
        r = self.post("/api/auth/login", {
            "username": "admin",
            "password": "admin123"
        })
        login_success = r.status_code == 200 and r.json().get('success')
        self.check(login_success, "管理员登录")

        # 4. 获取当前用户
        r = self.get("/api/auth/me")
        self.check(r.status_code == 200, "获取当前用户信息")
        if r.status_code == 200:
            user = r.json()
            print(f"    当前用户: {user.get('user', {}).get('display_name', 'N/A')}")

        # 5. 用户管理
        log_subsection("用户管理")
        r = self.get("/api/users")
        self.check(r.status_code == 200, "获取用户列表")
        if r.status_code == 200:
            print(f"    用户数量: {len(r.json())}")

        # 6. API密钥
        log_subsection("API密钥管理")
        r = self.get("/api/api-keys")
        self.check(r.status_code == 200, "获取API密钥列表")

        r = self.post("/api/api-keys", {
            "name": f"测试终端_{int(time.time())}",
            "role": "operate"
        })
        if r.status_code == 200:
            key = r.json().get('key', '')
            self.check(key.startswith('wh_'), "创建API密钥")
            print(f"    新密钥: {key[:20]}...")
        else:
            self.check(False, "创建API密钥")

    # ============ 第二阶段：联系方测试 ============
    def test_contacts(self):
        log_section("第二阶段：联系方管理测试")

        # 1. 联系方列表
        log_subsection("联系方列表")
        r = self.get("/api/contacts")
        self.check(r.status_code == 200, "获取联系方列表")
        if r.status_code == 200:
            data = r.json()
            print(f"    联系方数量: {data.get('total', 0)}")

        # 2. 创建供应商
        log_subsection("创建联系方")
        supplier_name = f"测试供应商_{int(time.time())}"
        r = self.post("/api/contacts", {
            "name": supplier_name,
            "phone": "13800138000",
            "is_supplier": True,
            "is_customer": False
        })
        self.check(r.status_code == 200, f"创建供应商: {supplier_name}")
        supplier_id = r.json().get('id') if r.status_code == 200 else None

        # 3. 创建客户
        customer_name = f"测试客户_{int(time.time())}"
        r = self.post("/api/contacts", {
            "name": customer_name,
            "email": "test@example.com",
            "is_supplier": False,
            "is_customer": True
        })
        self.check(r.status_code == 200, f"创建客户: {customer_name}")
        customer_id = r.json().get('id') if r.status_code == 200 else None

        # 4. 验证类型必选
        r = self.post("/api/contacts", {
            "name": "无类型联系方",
            "is_supplier": False,
            "is_customer": False
        })
        self.check(r.status_code == 400, "拒绝无类型联系方")

        # 5. 获取供应商/客户列表
        log_subsection("分类列表")
        r = self.get("/api/contacts/suppliers")
        self.check(r.status_code == 200, "获取供应商列表")

        r = self.get("/api/contacts/customers")
        self.check(r.status_code == 200, "获取客户列表")

        return supplier_id, customer_id

    # ============ 第三阶段：批次管理测试 ============
    def test_batch(self, supplier_id=None, customer_id=None):
        log_section("第三阶段：批次管理测试")

        # 获取一个测试物料
        log_subsection("准备测试物料")
        r = self.get("/api/materials?page_size=1")
        if r.status_code != 200 or not r.json().get('items'):
            log_fail("没有可用的测试物料")
            return

        material = r.json()['items'][0]
        material_name = material['name']
        print(f"    使用物料: {material_name} (当前库存: {material['quantity']})")

        # 1. 入库测试（创建批次）
        log_subsection("入库测试（自动创建批次）")
        stock_in_data = {
            "product_name": material_name,
            "quantity": 50,
            "reason": "批次测试入库",
            "operator": "测试员"
        }
        if supplier_id:
            stock_in_data["contact_id"] = supplier_id

        r = self.post("/api/materials/stock-in", stock_in_data)
        self.check(r.status_code == 200 and r.json().get('success'), "入库操作成功")

        if r.status_code == 200:
            data = r.json()
            batch = data.get('batch', {})
            batch_no_1 = batch.get('batch_no', 'N/A')
            print(f"    批次号: {batch_no_1}")
            print(f"    入库数量: {batch.get('quantity', 0)}")

        # 2. 再入库一批
        r = self.post("/api/materials/stock-in", {
            "product_name": material_name,
            "quantity": 30,
            "reason": "第二批入库"
        })
        if r.status_code == 200:
            batch_no_2 = r.json().get('batch', {}).get('batch_no', 'N/A')
            print(f"    第二批次号: {batch_no_2}")

        # 3. 出库测试（FIFO消耗）
        log_subsection("出库测试（FIFO批次消耗）")
        stock_out_data = {
            "product_name": material_name,
            "quantity": 60,
            "reason": "批次测试出库"
        }
        if customer_id:
            stock_out_data["contact_id"] = customer_id

        r = self.post("/api/materials/stock-out", stock_out_data)
        self.check(r.status_code == 200 and r.json().get('success'), "出库操作成功")

        if r.status_code == 200:
            data = r.json()
            consumptions = data.get('batch_consumptions', [])
            if consumptions:
                print(f"    批次消耗详情:")
                for c in consumptions:
                    print(f"      - {c['batch_no']}: 消耗 {c['quantity']}, 剩余 {c['remaining']}")
                # 验证FIFO
                total_consumed = sum(c['quantity'] for c in consumptions)
                self.check(total_consumed == 60, f"FIFO总消耗正确 ({total_consumed})")
            else:
                print(f"    (无批次消耗记录 - 可能是旧库存)")

        # 4. 验证记录包含批次信息
        log_subsection("验证记录批次信息")
        r = self.get("/api/inventory/records?page_size=5")
        self.check(r.status_code == 200, "获取进出库记录")

        if r.status_code == 200:
            items = r.json().get('items', [])
            has_batch = False
            for item in items:
                if item.get('batch_no') or item.get('batch_details'):
                    has_batch = True
                    break
            self.check(has_batch, "记录包含批次信息")

    # ============ Excel导出测试 ============
    def test_excel_export(self):
        log_section("Excel导出测试")

        log_subsection("导出功能")
        r = self.get("/api/inventory/export-excel")
        self.check(
            r.status_code == 200 and 'spreadsheet' in r.headers.get('content-type', ''),
            "导出进出库记录Excel"
        )

        r = self.get("/api/materials/export-excel")
        self.check(
            r.status_code == 200 and 'spreadsheet' in r.headers.get('content-type', ''),
            "导出物料清单Excel"
        )

    # ============ 权限测试 ============
    def test_permissions(self):
        log_section("权限控制测试")

        # 创建新session（未登录）
        guest_session = requests.Session()

        log_subsection("访客权限")
        # 访客可以读取
        r = guest_session.get(f"{self.base_url}/api/dashboard/stats", timeout=TIMEOUT)
        self.check(r.status_code == 200, "访客可读取仪表盘")

        r = guest_session.get(f"{self.base_url}/api/materials", timeout=TIMEOUT)
        self.check(r.status_code == 200, "访客可读取物料列表")

        # 访客不能写入
        r = guest_session.post(f"{self.base_url}/api/materials/stock-in", json={
            "product_name": "测试",
            "quantity": 10
        }, timeout=TIMEOUT)
        self.check(r.status_code in [401, 403], "访客不能入库")

    # ============ 运行所有测试 ============
    def run_all(self):
        print(f"\n{Colors.BOLD}仓库管理系统 v2.0 功能验证{Colors.RESET}")
        print(f"服务器: {self.base_url}\n")

        if not self.test_server_connection():
            print(f"\n{Colors.RED}测试终止：无法连接到服务器{Colors.RESET}")
            return False

        self.test_auth()
        supplier_id, customer_id = self.test_contacts()
        self.test_batch(supplier_id, customer_id)
        self.test_excel_export()
        self.test_permissions()

        # 汇总
        log_section("测试汇总")
        total = self.test_results["passed"] + self.test_results["failed"]
        passed = self.test_results["passed"]
        failed = self.test_results["failed"]

        print(f"  总计: {total} 项测试")
        print(f"  {Colors.GREEN}通过: {passed}{Colors.RESET}")
        if failed > 0:
            print(f"  {Colors.RED}失败: {failed}{Colors.RESET}")
        else:
            print(f"  失败: 0")

        success_rate = (passed / total * 100) if total > 0 else 0
        color = Colors.GREEN if success_rate == 100 else Colors.YELLOW if success_rate >= 80 else Colors.RED
        print(f"\n  {color}通过率: {success_rate:.1f}%{Colors.RESET}")

        return failed == 0


def start_server():
    """后台启动服务器"""
    print("正在启动后端服务器...")
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    process = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=backend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(3)  # 等待服务器启动
    return process


def main():
    auto_server = "--auto-server" in sys.argv

    server_process = None
    if auto_server:
        server_process = start_server()

    try:
        tester = WarehouseAPITest()
        success = tester.run_all()
        sys.exit(0 if success else 1)
    finally:
        if server_process:
            print("\n正在关闭服务器...")
            server_process.terminate()
            server_process.wait()


if __name__ == "__main__":
    main()
