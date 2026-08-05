"""外部 ERP 端到端：对着一个模拟客户 WMS 跑完整链路。

和其它测试的区别：这里**不 mock 我们自己的任何一层**，而是起一个真实的
「客户系统」（tests/fixtures/mock_wms/server.py，接口形状照抄现场探到的契约），
再走 Provider → MCP 工具 → 对方系统的完整调用。

重点验证一件此前没有覆盖到的事：**智能体绑定的外部仓库是否真的生效**。
模拟系统按仓库独立记账，所以绑不同仓库的两个智能体查到的库存必须不同、
各自的出入库也不能互相影响。早先的 mock 库存是全局的，这条根本验不出来。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mock_wms")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def mock_wms():
    """起一个模拟客户 WMS，返回其 base_url。

    启动失败**不 skip**：skip 在 CI 里是绿的，等于这一整个文件的断言可以无声消失。
    真起不来就 fail，并把子进程的 stderr 一起打出来——否则只能看到一句
    「未能启动」，连端口占用还是 import 报错都分不出来。
    """
    import tempfile as _tf

    port = _free_port()
    env = {**os.environ, "MOCK_WMS_PORT": str(port)}
    err = _tf.TemporaryFile()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_FIXTURE, "server.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=err)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            if proc.poll() is not None:
                break                       # 进程已经死了，不用再等
            try:
                urllib.request.urlopen(f"{base}/api/orgs", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            proc.kill()
            err.seek(0)
            pytest.fail(f"模拟 WMS 未能在 10s 内就绪；stderr:\n"
                        f"{err.read().decode('utf-8', 'replace')[:2000]}")
        if proc.poll() is not None:
            err.seek(0)
            pytest.fail(f"模拟 WMS 启动即退出（returncode={proc.returncode}）；stderr:\n"
                        f"{err.read().decode('utf-8', 'replace')[:2000]}")
        yield base
    finally:
        # terminate 给它机会正常收尾，超时才 kill；两条路径都要 wait，
        # 否则留下僵尸进程（同一 session 里跑多个模块时会累积）。
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        err.close()


def _stock(base, warehouse, code="SP-001"):
    raw = urllib.request.urlopen(
        f"{base}/api/products?warehouse={warehouse}", timeout=5).read()
    for p in json.loads(raw)["data"]:
        if p["code"] == code:
            return p["stock"]
    return None


def _provider(base, external_warehouse_id=None, external_tenant_id=None):
    """按连接级绑定实例化 Provider —— 与运行时注入的配置形状一致。"""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    from providers.test_runner import load_provider_from_file
    cfg = {"api_base_url": base, "timeout": 10}
    if external_warehouse_id:
        cfg["external_warehouse_id"] = external_warehouse_id
    if external_tenant_id:
        cfg["external_tenant_id"] = external_tenant_id
    return load_provider_from_file(os.path.join(_FIXTURE, "provider.py"), cfg)


class TestExternalWarehouseBinding:
    """绑定必须真的生效——这是整套外部作用域设计的核心断言。"""

    def test_different_bindings_see_different_stock(self, mock_wms):
        bj = _provider(mock_wms, "WH-BJ-01", "ORG-BJ")
        sh = _provider(mock_wms, "WH-SH-01", "ORG-SH")
        r_bj = bj.query_stock("矿泉水")
        r_sh = sh.query_stock("矿泉水")
        assert r_bj["success"] and r_sh["success"]
        assert r_bj["product"]["current_stock"] != r_sh["product"]["current_stock"], (
            "两个绑定看到同样的库存——说明 external_warehouse_id 没有真正生效")

    def test_stock_in_only_affects_bound_warehouse(self, mock_wms):
        before_bj = _stock(mock_wms, "WH-BJ-01")
        before_sh = _stock(mock_wms, "WH-SH-01")

        bj = _provider(mock_wms, "WH-BJ-01", "ORG-BJ")
        resp = bj.stock_in("矿泉水", 7, "purchase", "隔离验证", "张三", True)
        assert resp["success"], resp

        assert _stock(mock_wms, "WH-BJ-01") == before_bj + 7
        assert _stock(mock_wms, "WH-SH-01") == before_sh, (
            "入库影响到了没绑定的仓库——串仓了")

    def test_unbound_provider_does_not_send_invented_warehouse(self, mock_wms):
        """没有绑定时不能臆造 warehouse 编码。

        早先 Provider 在无绑定时回落到字面量 "default"，对方判为"未知仓库"，
        连 L1 连通性测试都过不去。无绑定应当不传该参数、由对方用自己的默认仓。
        """
        p = _provider(mock_wms)          # 不传任何 external_*
        assert p.query_stock("矿泉水")["success"] is True
        assert p.search(None, "material", None, None, None, True)["success"] is True
        assert p.get_today_statistics()["success"] is True


class TestExternalScopeDiscovery:
    """探测接口返回的内容必须按组织正确过滤。"""

    def test_tenants_and_warehouses(self, mock_wms):
        p = _provider(mock_wms)
        orgs = p.list_tenants()
        assert orgs["success"] and {o["id"] for o in orgs["items"]} == {"ORG-BJ", "ORG-SH"}

        bj = p.list_warehouses("ORG-BJ")
        sh = p.list_warehouses("ORG-SH")
        assert {w["id"] for w in bj["items"]} == {"WH-BJ-01", "WH-BJ-02"}
        assert {w["id"] for w in sh["items"]} == {"WH-SH-01"}

    def test_users_scoped_by_org(self, mock_wms):
        p = _provider(mock_wms)
        bj = {u["name"] for u in p.list_users("ORG-BJ")["items"]}
        sh = {u["name"] for u in p.list_users("ORG-SH")["items"]}
        assert bj and sh and not (bj & sh), "两个组织的账号出现了交集"


class TestOutOfStockContract:
    def test_insufficient_stock_is_structured_failure(self, mock_wms):
        p = _provider(mock_wms, "WH-BJ-01", "ORG-BJ")
        r = p.stock_out("签字笔", 99999, "consume", "超卖", "张三", True)
        assert r["success"] is False
        assert r["error"] == "insufficient_stock"
        assert "库存不足" in r["message"]


# ---------------------------------------------------------------------------
# 走完整运行时链路的绑定验证
# ---------------------------------------------------------------------------
# 上面那些用例是直接把 external_warehouse_id 塞进 Provider 的构造配置里，
# 只证明了「Provider 自己会读这个键」。而真实链路是：
#
#   连接配置 → create_runtime_state(external_warehouse_id=...) → state['config']
#          → _load_provider_from_db_or_default(default_config)
#          → merged_config = {**default_config, **stored_config}   ← 危险的一步
#          → load_provider_from_file(filepath, merged_config)
#
# 真正出过的 bug 就在 merge 那一步：Provider 自身存的静态 config 里若也写了
# external_warehouse_id，会把连接级绑定盖掉，所有智能体一起打到同一个外部仓库。
# 绕开这条链路的断言抓不到它，所以这里从 create_runtime_state 开始走。

class TestBindingSurvivesRuntimeChain:

    @staticmethod
    def _fake_backend(port, provider_filename, tenant_id, stored_config):
        """最小后端，只实现 MCP 引导要打的那个接口。"""
        import http.server
        import threading

        payload = json.dumps({
            "mode": "external_erp",
            "provider": {
                "provider_name": "mock_wms_e2e",
                "filename": provider_filename,
                "tenant_id": tenant_id,
                "config": stored_config,
            },
        }).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if "active-for-mcp" not in self.path:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def test_connection_binding_beats_provider_stored_config(self, mock_wms):
        """连接绑 WH-BJ-01，而 Provider 存的静态 config 指向 WH-SH-01。

        必须看到 BJ 的库存。看到 SH 的就说明 merge 把连接级绑定盖掉了。
        """
        import shutil
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
        import warehouse_mcp

        tenant_id = 1
        custom_dir = os.path.join(
            os.path.dirname(warehouse_mcp.__file__), "providers", "custom", str(tenant_id))
        os.makedirs(custom_dir, exist_ok=True)
        fname = f"bindchain_{os.getpid()}.py"
        dest = os.path.join(custom_dir, fname)

        # 拷贝动作本身必须在 try 里：copyfile 之后、_fake_backend 之前若抛异常
        # （端口占用等），文件就留在 providers/custom/ 下了。那个目录会被
        # providers/__init__.py 的 _discover() 扫描注册，残留会污染后续用例；
        # 这个目录残留的 .py 也正是被误提交进仓库两次的东西。
        srv = None
        try:
            shutil.copyfile(os.path.join(_FIXTURE, "provider.py"), dest)
            port = _free_port()
            srv = self._fake_backend(
                port, fname, tenant_id,
                # Provider 自身存的静态配置，故意指向另一个仓库
                {"api_base_url": mock_wms, "timeout": 10,
                 "external_warehouse_id": "WH-SH-01", "external_tenant_id": "ORG-SH"})

            state = warehouse_mcp.create_runtime_state(
                f"http://127.0.0.1:{port}/api",
                "test-key",
                external_tenant_id="ORG-BJ",
                external_warehouse_id="WH-BJ-01",
            )
            # 注意：state 的 api_base_url 必须留给**我方后端**（引导接口在那儿）。
            # Provider 要访问的对方地址是从 stored_config 里 merge 进去的，
            # 两者不是一回事——早先这里手抖改成了对方地址，引导请求打到模拟 WMS
            # 上拿到 404，于是静默回退默认 Provider，断言直接崩在属性缺失上。
            with warehouse_mcp.runtime_context(state):
                provider = warehouse_mcp._get_provider()
                assert provider.__class__.__name__ != "WarehouseAPIProvider", \
                    "回退到默认 Provider 了，说明文件没找到或引导接口没打通"
                assert provider.warehouse_id == "WH-BJ-01", (
                    f"连接绑定被 Provider 静态配置盖掉了：{provider.warehouse_id}")

                r = provider.query_stock("矿泉水")
                assert r["success"], r
                assert r["product"]["current_stock"] == _stock(mock_wms, "WH-BJ-01"), \
                    "查到的不是绑定仓库的库存"
                assert r["product"]["current_stock"] != _stock(mock_wms, "WH-SH-01")
        finally:
            if srv is not None:
                srv.shutdown()
            os.path.exists(dest) and os.unlink(dest)
            # 动态 import 会在同目录留下 __pycache__/bindchain_*.pyc，
            # 同样会被 _discover() 之外的导入路径捡到，一并收干净。
            import glob
            for pyc in glob.glob(os.path.join(
                    custom_dir, "__pycache__", f"{fname[:-3]}*.pyc")):
                os.path.exists(pyc) and os.unlink(pyc)
