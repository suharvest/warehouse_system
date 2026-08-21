"""对端数据体检（POST /api/erp/providers/{id}/probe）

回归的是一个真实事故：某备品系统在不带过滤参数时返回一条只有 partType 的占位
记录，Provider 把它当成唯一候选，于是所有查询都返回 not_found —— 而 Level 1
依然全绿，因为它只检查返回 dict 里有没有 ``success`` 这个 key。体检就是补这个
语义缺口的，所以这里必须同时断言「Level 1 绿」和「体检红」能并存。
"""

import json
import os
import sys
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(REPO_ROOT, 'mcp')
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)


# ── 两个假 Provider：都符合 BaseProvider 结构，区别只在能不能查出数据 ──

_HEALTHY = '''
try:
    from providers.base import BaseProvider
except ImportError:
    from ..base import BaseProvider


class HealthyProvider(BaseProvider):
    PROVIDER_NAME = "probe_healthy"

    _ITEMS = [
        {"id": 1, "name": "撬具", "sku": "P001", "spec": "LH-815",
         "unit": "件", "current_stock": 25, "safe_stock": 10, "status": "正常"},
    ]

    def resolve_name(self, text, entity_type="all"):
        cand = {"id": 1, "name": "撬具", "type": "material", "score": 1.0,
                "extra": {"sku": "P001", "variant": "LH-815", "stock": 25}}
        return {"best_match": cand, "confident": True, "candidates": [cand]}

    def query_stock(self, product_name, show_batches=False):
        return {"success": True, "product": {
            "name": "撬具", "sku": "P001", "current_stock": 25, "unit": "件",
            "safe_stock": 10, "location": "LH-815", "status": "正常"},
            "message": "ok"}

    def stock_in(self, *a, **k):
        return {"success": True}

    def stock_out(self, *a, **k):
        return {"success": True}

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        return {"success": True, "count": 1, "total": 1,
                "items": list(self._ITEMS), "message": "ok"}

    def get_today_statistics(self):
        return {"success": True, "statistics": {"today_in": 0, "today_out": 0,
                "net_change": 0, "total_stock": 25, "low_stock_count": 0},
                "message": "ok"}
'''

# 复刻事故形态：结构完全合规，但目录里只有空壳记录、任何查询都 not_found。
_HOLLOW = '''
try:
    from providers.base import BaseProvider
except ImportError:
    from ..base import BaseProvider


class HollowProvider(BaseProvider):
    PROVIDER_NAME = "probe_hollow"

    def resolve_name(self, text, entity_type="all"):
        return {"best_match": None, "confident": False, "candidates": []}

    def query_stock(self, product_name, show_batches=False):
        return {"success": False, "error": "not_found",
                "message": "未找到备品：%s" % product_name}

    def stock_in(self, *a, **k):
        return {"success": False, "error": "not_found", "message": "未找到"}

    def stock_out(self, *a, **k):
        return {"success": False, "error": "not_found", "message": "未找到"}

    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0):
        if query:
            return {"success": True, "count": 0, "total": 0, "items": [], "message": "ok"}
        # 不带查询词时回一条空壳（对端占位响应被原样包成候选）
        return {"success": True, "count": 1, "total": 1, "items": [
            {"id": None, "name": "", "sku": "", "spec": "测试数据",
             "unit": "件", "current_stock": 0, "safe_stock": 10, "status": "缺货"}
        ], "message": "ok"}

    def get_today_statistics(self):
        return {"success": True, "statistics": {"today_in": 0, "today_out": 0,
                "net_change": 0, "total_stock": 0, "low_stock_count": 0},
                "message": "ok"}
'''


def _upload(admin_client, source: str, filename: str) -> int:
    resp = admin_client.post(
        "/api/erp/providers",
        files={"file": (filename, textwrap.dedent(source).encode("utf-8"), "text/x-python")},
    )
    if resp.status_code == 409:
        # 同名 Provider 已由前一个用例上传（DB 按用例回滚，落盘的 .py 不回滚）——
        # 复用那一行即可，文件内容是一样的。
        name = os.path.splitext(filename)[0]
        listing = admin_client.get("/api/erp/providers").json()["providers"]
        provider_id = next(p["id"] for p in listing if p["provider_name"] == name)
    else:
        assert resp.status_code == 200, resp.text
        provider_id = resp.json()["id"]
    admin_client.put(f"/api/erp/providers/{provider_id}",
                     json={"config": {"api_base_url": "http://127.0.0.1:9", "timeout": 5}})
    return provider_id


def _probe(admin_client, provider_id, sample=""):
    resp = admin_client.post(f"/api/erp/providers/{provider_id}/probe",
                             json={"sample": sample})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _status_of(result, check_id):
    return next(c["status"] for c in result["checks"] if c["id"] == check_id)


def _upload_and_cleanup(admin_client, source, filename):
    """上传，用完把落盘的 .py 删掉。

    DB 行随用例回滚，但文件不会——留在 custom/ 里会被下次进程启动时的
    ``_discover()`` 注册成真实 Provider。
    """
    provider_id = _upload(admin_client, source, filename)
    yield provider_id
    admin_client.delete(f"/api/erp/providers/{provider_id}")
    for tenant_dir in ("1", ""):
        path = os.path.join(MCP_DIR, "providers", "custom", tenant_dir, filename)
        if os.path.exists(path):
            os.remove(path)


@pytest.fixture()
def healthy_provider(admin_client):
    yield from _upload_and_cleanup(admin_client, _HEALTHY, "probe_healthy.py")


@pytest.fixture()
def hollow_provider(admin_client):
    yield from _upload_and_cleanup(admin_client, _HOLLOW, "probe_hollow.py")


def test_probe_passes_on_healthy_provider(admin_client, healthy_provider):
    result = _probe(admin_client, healthy_provider, sample="撬具")
    assert result["all_passed"] is True, result
    assert [_status_of(result, cid) for cid in ("P1", "P2", "P3", "P4", "P5")] == \
        ["pass"] * 5


def test_probe_catches_hollow_catalog_that_level1_lets_through(
    admin_client, hollow_provider
):
    """核心回归：Level 1 全绿，体检必须红。"""
    l1 = admin_client.post(f"/api/erp/providers/{hollow_provider}/test?level=1")
    assert l1.status_code == 200, l1.text
    assert l1.json()["all_passed"] is True, "前提变了：这个 Provider 本该骗过 Level 1"

    result = _probe(admin_client, hollow_provider, sample="撬具")
    assert result["all_passed"] is False
    assert _status_of(result, "P1") == "fail"   # 空壳目录
    assert _status_of(result, "P2") == "fail"   # 解析不出候选
    assert _status_of(result, "P3") == "fail"   # 查不到库存
    assert _status_of(result, "P4") == "fail"   # 搜不到
    assert _status_of(result, "P5") == "skip"
    assert "空壳" in _status_detail(result, "P1")


def _status_detail(result, check_id):
    return next(c["detail"] for c in result["checks"] if c["id"] == check_id)


def test_probe_without_sample_only_runs_catalog(admin_client, healthy_provider):
    result = _probe(admin_client, healthy_provider, sample="")
    assert result["sample"] == ""
    assert _status_of(result, "P1") == "pass"
    for cid in ("P2", "P3", "P4", "P5"):
        assert _status_of(result, cid) == "skip"


def test_probe_result_is_persisted_alongside_level_results(
    admin_client, healthy_provider
):
    admin_client.post(f"/api/erp/providers/{healthy_provider}/test?level=1")
    _probe(admin_client, healthy_provider, sample="撬具")

    row = admin_client.get("/api/erp/providers").json()
    provider = next(p for p in row["providers"] if p["id"] == healthy_provider)
    tr = provider["test_results"]
    if isinstance(tr, str):
        tr = json.loads(tr)
    assert tr["probe"]["all_passed"] is True
    assert tr["level1"]["all_passed"] is True, "写 probe 不能把 level1 结果冲掉"


def test_probe_requires_auth(client, healthy_provider):
    resp = client.post(f"/api/erp/providers/{healthy_provider}/probe",
                       json={"sample": "撬具"})
    assert resp.status_code in (401, 403)


def test_probe_on_unknown_provider_returns_404(admin_client):
    resp = admin_client.post("/api/erp/providers/999999/probe", json={"sample": "x"})
    assert resp.status_code == 404


class TestNullRefDetection:
    """对端抛空引用时，体检要给出「key 必须存在」这条可执行的方向。

    现场：整套 ERP 接口因为我们省略了「可选」字段的 key 而全线不可用，症状只是
    列表拉不到。泛泛报一句「对端不支持列表拉取」会把人引向错误方向。
    """

    def _probe_catalog(self, message):
        from providers.probe import _probe_catalog

        class P:
            def search(self, *a, **k):
                return {"success": False, "message": message}

        return _probe_catalog(P())

    @pytest.mark.parametrize("msg", [
        "未将对象引用设置到对象的实例。",
        "System.NullReferenceException: at Parts.Query()",
        "Object reference not set to an instance of an object",
    ])
    def test_null_ref_is_fail_with_actionable_hint(self, msg):
        r = self._probe_catalog(msg)
        assert r["status"] == "fail", "空引用是硬故障，不该只报 warn"
        assert "key" in r["detail"], "必须点出 key 必须存在这条线索"
        assert "逐个删 key" in r["detail"], "必须给出自查方法"

    def test_ordinary_business_error_stays_warn(self):
        """普通业务错误不该被误升级成 fail。"""
        r = self._probe_catalog("该接口未开放")
        assert r["status"] == "warn"
        assert "key" not in r["detail"]
