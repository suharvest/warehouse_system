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
    requests.exceptions.ReadTimeout("read timeout"),
])
def test_transport_error_returns_speakable_failure(provider, clock, monkeypatch, exc):
    post, calls = _counting_post(exc=exc)
    monkeypatch.setattr(requests, "post", post)

    resp = provider.http_post("/stock", {"q": 1})

    assert resp["success"] is False
    assert resp["error"] == "backend_unreachable"
    assert resp["message"]          # 可直接播报的中文
    assert len(calls) == 1


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
