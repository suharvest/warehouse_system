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
    """起一个模拟客户 WMS，返回其 base_url。"""
    port = _free_port()
    env = {**os.environ, "MOCK_WMS_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_FIXTURE, "server.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{base}/api/orgs", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.skip("模拟 WMS 未能启动")
    yield base
    proc.kill()
    proc.wait(timeout=5)


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
