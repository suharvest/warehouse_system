"""BaseProvider 的「后端不可达熔断」。

没有熔断时，外部后端整个挂掉会让每次工具调用都先卡满 connect_timeout 才失败，
语音侧表现为长时间无响应；连续几个工具调用就是十几秒。熔断把冷却期内的调用
变成毫秒级返回，并给出一句能直接播报的失败话术。

只有传输层错误（连接被拒/超时/DNS）才熔断 —— 那说明对方整个不在。
HTTP 4xx/5xx 和业务失败（返回体 success=false）不熔断：对方是活的，
换个参数下一次可能就成功。
"""

import os
import sys

import pytest
import requests

_MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from providers.base import BaseProvider  # noqa: E402


class _Provider(BaseProvider):
    """最小可实例化 Provider：6 个抽象方法给空实现。"""

    PROVIDER_NAME = "breaker_test"

    def resolve_name(self, text, entity_type="all"):
        return {}

    def query_stock(self, product_name, show_batches=False):
        return {}

    def stock_in(self, *a, **k):
        return {}

    def stock_out(self, *a, **k):
        return {}

    def search(self, *a, **k):
        return {}

    def get_today_statistics(self):
        return {}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture
def provider():
    return _Provider({"api_base_url": "http://backend.invalid/api"})


@pytest.fixture
def clock(monkeypatch):
    """可控的 monotonic 时钟：冷却窗口的测试不能靠 sleep。"""
    state = {"now": 1000.0}

    import providers.base as base_mod
    monkeypatch.setattr(base_mod.time, "monotonic", lambda: state["now"])
    return state


def _counting_post(exc=None, response=None):
    calls = []

    def _post(url, **kwargs):
        calls.append(url)
        if exc is not None:
            raise exc
        return response

    return _post, calls


@pytest.mark.parametrize("exc", [
    requests.exceptions.ConnectionError("connection refused"),
    requests.exceptions.ConnectTimeout("connect timeout"),
])
def test_unreachable_error_returns_speakable_failure(provider, clock, monkeypatch, exc):
    """请求没送达：可以断言后端没动过。"""
    post, calls = _counting_post(exc=exc)
    monkeypatch.setattr(requests, "post", post)

    resp = provider.http_post("/stock", {"q": 1})

    assert resp["success"] is False
    assert resp["error"] == "backend_unreachable"
    assert resp["message"]          # 可直接播报的中文
    assert "executed" not in resp
    assert len(calls) == 1


def test_read_timeout_is_execution_unknown(provider, clock, monkeypatch):
    """读超时：请求已送达，写操作可能已生效，不能断言"未执行"。"""
    post, calls = _counting_post(exc=requests.exceptions.ReadTimeout("read timeout"))
    monkeypatch.setattr(requests, "post", post)

    resp = provider.http_post("/stock-out", {"q": 1})

    assert resp["success"] is False
    assert resp["error"] == "backend_timeout"
    assert resp["executed"] == "unknown"
    assert "执行结果未知" in resp["message"]
    assert len(calls) == 1


def test_read_timeout_also_trips_breaker(provider, clock, monkeypatch):
    post, calls = _counting_post(exc=requests.exceptions.ReadTimeout("read timeout"))
    monkeypatch.setattr(requests, "post", post)

    provider.http_post("/stock", {})
    clock["now"] += 5
    resp = provider.http_post("/stock", {})

    assert len(calls) == 1, "冷却期内不应再发请求"
    # 短路时请求确实没发出去，语义回到"明确未发送"
    assert resp["error"] == "backend_unreachable"
    assert "executed" not in resp


def test_concurrent_failures_send_one_real_request(provider, monkeypatch):
    """8 线程并发：第一个真实请求失败后，其余全部短路，不再打后端。

    熔断状态是跨线程共享的（FastMCP 用 to_thread 跑同步工具），
    读-改-写不加锁时「过期清零」会抹掉刚写入的截止时刻。
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    calls = []
    calls_lock = threading.Lock()
    tripped = threading.Event()

    def _post(url, **kwargs):
        with calls_lock:
            calls.append(url)
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests, "post", _post)

    start = threading.Barrier(8)

    def _worker(idx):
        start.wait(timeout=5)
        if idx != 0:
            # 其余线程等第一次真实失败落地后再并发进来
            tripped.wait(timeout=5)
        try:
            return provider.http_post("/stock", {})
        finally:
            if idx == 0:
                tripped.set()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_worker, range(8)))

    assert len(calls) == 1, f"只应发出 1 次真实请求，实际 {len(calls)}"
    assert all(r["error"] == "backend_unreachable" for r in results)
    assert provider._backend_down_remaining() > 0


def test_expired_clear_does_not_clobber_fresh_trip(provider, clock, monkeypatch):
    """过期清零只能清自己看到的那个值，不能抹掉别的线程刚设的熔断。"""
    post, _ = _counting_post(exc=requests.exceptions.ConnectionError("down"))
    monkeypatch.setattr(requests, "post", post)

    provider.http_post("/stock", {})
    clock["now"] += 25                      # 旧窗口已过期
    provider._backend_down_until = clock["now"] + 20   # 新线程刚设的熔断
    assert provider._backend_down_remaining() == 20


def test_no_request_during_cooldown(provider, clock, monkeypatch):
    post, calls = _counting_post(exc=requests.exceptions.ConnectionError("down"))
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", post)

    provider.http_post("/stock", {})
    assert len(calls) == 1

    clock["now"] += 5          # 默认冷却 20s，还在窗口内
    for _ in range(3):
        resp = provider.http_post("/stock", {})
        assert resp["error"] == "backend_unreachable"
    resp = provider.http_get("/stock")
    assert resp["error"] == "backend_unreachable"

    assert len(calls) == 1, "冷却期内不应再发出任何请求"


def test_recovers_after_cooldown(provider, clock, monkeypatch):
    fail_post, fail_calls = _counting_post(
        exc=requests.exceptions.ConnectionError("down")
    )
    monkeypatch.setattr(requests, "post", fail_post)
    provider.http_post("/stock", {})

    ok_post, ok_calls = _counting_post(
        response=_FakeResponse(200, {"success": True, "product": {"name": "阀门"}})
    )
    monkeypatch.setattr(requests, "post", ok_post)

    clock["now"] += 21         # 冷却结束
    resp = provider.http_post("/stock", {})

    assert len(ok_calls) == 1, "冷却结束后必须真的再发请求"
    assert resp == {"success": True, "product": {"name": "阀门"}}


@pytest.mark.parametrize("status,payload", [
    (200, {"success": False, "error": "not_found", "message": "没有这个物料"}),
    (500, {"detail": "internal error"}),
    (404, {"detail": "no such endpoint"}),
])
def test_business_and_http_failures_do_not_trip(
    provider, clock, monkeypatch, status, payload
):
    post, calls = _counting_post(response=_FakeResponse(status, payload))
    monkeypatch.setattr(requests, "post", post)

    first = provider.http_post("/stock", {})
    second = provider.http_post("/stock", {})

    assert len(calls) == 2, "对方是活的，后续请求必须照发"
    for resp in (first, second):
        assert resp.get("error") != "backend_unreachable"


def test_cooldown_is_configurable(clock, monkeypatch):
    provider = _Provider({
        "api_base_url": "http://backend.invalid/api",
        "backend_down_cooldown_sec": 3,
    })
    post, calls = _counting_post(exc=requests.exceptions.ConnectionError("down"))
    monkeypatch.setattr(requests, "post", post)

    provider.http_post("/stock", {})
    clock["now"] += 4          # 超过 3s 冷却
    provider.http_post("/stock", {})

    assert len(calls) == 2


class TestSpeakableSemantics:
    """播报层必须把两类失败说成不同的话。

    「没连上」可以告诉用户库存没动；「发出去了但没回音」不行 —— 后端
    可能已经扣了，说"没扣"会让用户重复出库。
    """

    @staticmethod
    def _wrap(operation, resp):
        import importlib
        mcp_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"
        )
        if mcp_dir not in sys.path:
            sys.path.insert(0, mcp_dir)
        return importlib.import_module("warehouse_mcp")._wrap_response(
            operation, resp
        )

    def _stock_out_via_provider(self, provider, monkeypatch, exc):
        """走真实 http_post → Provider 失败结构 → 播报层，不手搓 dict。"""
        post, _ = _counting_post(exc=exc)
        monkeypatch.setattr(requests, "post", post)

        class _MockProvider(type(provider)):
            def stock_out(self, *a, **k):
                return self.http_post("/stock-out", {"name": "四通阀", "qty": 3})

        p = _MockProvider(provider.config)
        return self._wrap("stock_out", p.stock_out())

    def test_read_timeout_say_is_execution_unknown(self, provider, monkeypatch):
        out = self._stock_out_via_provider(
            provider, monkeypatch, requests.exceptions.ReadTimeout("read timeout")
        )
        assert out["ok"] is False
        assert out["executed"] == "unknown"
        assert "执行结果未知" in out["say"]
        assert "没有扣任何库存" not in out["say"]
        assert "未执行" not in out["say"]
        assert "库存没有任何变化" not in out.get("notice", "")

    def test_unreachable_say_states_not_executed(self, provider, monkeypatch):
        out = self._stock_out_via_provider(
            provider, monkeypatch, requests.exceptions.ConnectTimeout("connect")
        )
        assert out["ok"] is False
        assert out["executed"] is False
        assert "未执行" in out["say"]
        assert "连不上" in out["say"]
