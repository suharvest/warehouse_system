"""Regression tests for mcp_pipe.py's pending-request-aware timeout.

Bug history: TOOL_CALL_TIMEOUT used to fire on any 60s gap in subprocess stdout
output, which raced the cloud's ~60s keepalive ping interval and killed the
subprocess during idle periods (causing reconnect storms). The pipe now only
enforces TOOL_CALL_TIMEOUT when there's an outstanding request in
pending_requests; idle periods must not trigger a timeout.
"""
import asyncio
import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp"))
import mcp_pipe  # noqa: E402


# ----------------------------- classifier ----------------------------------

def test_classify_request():
    assert mcp_pipe._classify_json_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ) == ("request", 1)


def test_classify_notification_has_no_id():
    assert mcp_pipe._classify_json_rpc(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    ) == ("notification", None)


def test_classify_response_with_result():
    assert mcp_pipe._classify_json_rpc(
        {"jsonrpc": "2.0", "id": 1, "result": {}}
    ) == ("response", 1)


def test_classify_response_with_error():
    assert mcp_pipe._classify_json_rpc(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "x"}}
    ) == ("response", 1)


def test_classify_garbage():
    assert mcp_pipe._classify_json_rpc("not a dict") == (None, None)
    assert mcp_pipe._classify_json_rpc({}) == (None, None)
    assert mcp_pipe._classify_json_rpc({"only": "stuff"}) == (None, None)


def test_json_rpc_summary_excludes_payload_and_secrets():
    parsed = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"api_key": "must-not-leak", "arguments": {"query": "secret"}},
    }

    summary = mcp_pipe._json_rpc_summary(parsed, 123)

    assert summary == "request method=tools/call id=7 bytes=123"
    assert "must-not-leak" not in summary
    assert "secret" not in summary


def test_json_rpc_summary_preserves_zero_id():
    summary = mcp_pipe._json_rpc_summary(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize"}, 205
    )

    assert summary == "request method=initialize id=0 bytes=205"


def test_reconnect_delay_is_jittered_and_capped(monkeypatch):
    monkeypatch.setattr(mcp_pipe.random, "uniform", lambda low, high: high)

    assert mcp_pipe._reconnect_delay(10) == 15
    assert mcp_pipe._reconnect_delay(60) == mcp_pipe.MAX_BACKOFF


def test_build_log_target_includes_connection_labels(monkeypatch, tmp_path):
    target = tmp_path / "warehouse_mcp.py"
    target.write_text("# fake")
    labels = {
        "MCP_LOG_CONN_ID": "conn-1",
        "MCP_LOG_NAME": "天津\n连接",
        "MCP_LOG_TENANT_ID": "2246124",
        "MCP_LOG_TENANT_NAME": "租户-2246124",
        "MCP_LOG_WAREHOUSE_ID": "1",
        "MCP_LOG_WAREHOUSE_NAME": "默认仓库",
    }
    for key, value in labels.items():
        monkeypatch.setenv(key, value)

    log_target = mcp_pipe.build_log_target(str(target))

    assert "conn_id=conn-1" in log_target
    assert "name=天津 连接" in log_target
    assert "tenant_id=2246124" in log_target
    assert "tenant=租户-2246124" in log_target
    assert "warehouse_id=1" in log_target
    assert "warehouse=默认仓库" in log_target
    assert "target=warehouse_mcp.py" in log_target
    assert "\n" not in log_target
    assert "token=" not in log_target


# ---------------------------- ws-to-process --------------------------------

class FakeWebSocket:
    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []

    async def recv(self):
        if not self._incoming:
            await asyncio.sleep(3600)  # park
        return self._incoming.pop(0)

    async def send(self, msg):
        self.sent.append(msg)


class _StdinCapture:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, s):
        self.written.append(s)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProcessNoStdout:
    """For ws-to-process tests we only need .stdin."""
    def __init__(self):
        self.stdin = _StdinCapture()


async def _drain_ws(ws, proc, pending):
    task = asyncio.create_task(
        mcp_pipe.pipe_websocket_to_process(ws, proc, "test", pending)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await task


def test_ws_to_process_records_request_id():
    ws = FakeWebSocket([
        json.dumps({"jsonrpc": "2.0", "id": 42, "method": "tools/list"}),
    ])
    proc = _FakeProcessNoStdout()
    pending = {}
    asyncio.run(_drain_ws(ws, proc, pending))
    assert 42 in pending


def test_ws_to_process_skips_notifications():
    ws = FakeWebSocket([
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
    ])
    proc = _FakeProcessNoStdout()
    pending = {}
    asyncio.run(_drain_ws(ws, proc, pending))
    assert pending == {}


def test_ws_to_process_skips_responses():
    ws = FakeWebSocket([
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
    ])
    proc = _FakeProcessNoStdout()
    pending = {}
    asyncio.run(_drain_ws(ws, proc, pending))
    assert pending == {}


# ----------------- process-to-ws: the regression -----------------------------

def _spawn_idle_subprocess():
    """Spawn a python subprocess that just blocks on stdin (never writes).

    Killing it lets the readline thread return EOF cleanly, avoiding pytest hangs.
    """
    return subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )


def test_idle_pipe_does_not_timeout():
    """REGRESSION: with no pending requests, pipe_process_to_websocket must NOT
    kill the subprocess even if subprocess produces no output longer than
    TOOL_CALL_TIMEOUT. This was the root cause of the ~60s reconnect loop."""
    proc = _spawn_idle_subprocess()
    ws = FakeWebSocket([])
    pending: dict = {}

    original_timeout = mcp_pipe.TOOL_CALL_TIMEOUT
    mcp_pipe.TOOL_CALL_TIMEOUT = 0.3
    try:
        async def runner():
            task = asyncio.create_task(
                mcp_pipe.pipe_process_to_websocket(proc, ws, "test", pending)
            )
            await asyncio.sleep(1.2)  # 4× the (mocked) timeout
            # Subprocess must still be alive — no timeout-induced kill.
            still_alive = proc.poll() is None
            # Kill subprocess BEFORE cancelling: readline runs in a thread which
            # asyncio cannot interrupt; only EOF (from kill) lets it return.
            proc.kill()
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
            return still_alive

        still_alive = asyncio.run(runner())
        assert still_alive, "subprocess was killed during idle — regression!"
        assert ws.sent == [], "no JSON-RPC error should be sent during idle"
    finally:
        mcp_pipe.TOOL_CALL_TIMEOUT = original_timeout
        proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)


def test_pending_request_triggers_timeout():
    """With an outstanding request and no subprocess output within the timeout,
    the pipe must kill the subprocess and emit a JSON-RPC error to the WS."""
    proc = _spawn_idle_subprocess()
    ws = FakeWebSocket([])
    pending: dict = {99: time.monotonic()}

    original_timeout = mcp_pipe.TOOL_CALL_TIMEOUT
    mcp_pipe.TOOL_CALL_TIMEOUT = 0.3
    try:
        async def runner():
            task = asyncio.create_task(
                mcp_pipe.pipe_process_to_websocket(proc, ws, "test", pending)
            )
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(runner())

        # Side effects: subprocess killed + one JSON-RPC error sent for id=99.
        assert proc.poll() is not None, "subprocess should have been killed"
        assert len(ws.sent) == 1
        payload = json.loads(ws.sent[0])
        assert payload["id"] == 99
        assert payload["error"]["code"] == -32001
    finally:
        mcp_pipe.TOOL_CALL_TIMEOUT = original_timeout
        # proc already killed by pipe, but be defensive
        if proc.poll() is None:
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)


def test_interleaved_notification_and_request():
    """A notification between requests must not pollute pending_requests."""
    ws = FakeWebSocket([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ])
    proc = _FakeProcessNoStdout()
    pending: dict = {}
    asyncio.run(_drain_ws(ws, proc, pending))
    assert set(pending.keys()) == {1, 2}, f"expected {{1,2}}, got {set(pending.keys())}"


def test_unparseable_subprocess_output_does_not_crash():
    """Subprocess might log garbage or non-JSON debug lines; pipe should forward
    them anyway and not raise. Pending stays untouched."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "print('not json at all', flush=True); import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    ws = FakeWebSocket([])
    pending: dict = {99: time.monotonic()}  # pre-existing pending stays
    try:
        async def runner():
            task = asyncio.create_task(
                mcp_pipe.pipe_process_to_websocket(proc, ws, "test", pending)
            )
            await asyncio.sleep(0.3)
            proc.kill()
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        asyncio.run(runner())
        # Garbage line forwarded to WS (mcp_pipe doesn't filter)
        assert len(ws.sent) >= 1
        assert "not json" in ws.sent[0]
        # Pending unchanged (no response id 99 was seen)
        assert 99 in pending
    finally:
        if proc.poll() is None:
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)


def test_oldest_request_deadline_used_not_per_readline_gap():
    """REGRESSION: if a fast ping reply arrives 1ms before timeout, the
    per-readline timeout would reset and never catch the slow original request.
    With per-request deadlines, the original slow request's deadline is honored."""
    # Subprocess emits a single fast response after 0.1s, then blocks. The
    # slow request (id=1) was registered at T=0; its deadline is T+0.4.
    fast_response = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {}})
    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import time, sys; time.sleep(0.1); print({fast_response!r}, flush=True); sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    ws = FakeWebSocket([])
    now = time.monotonic()
    pending: dict = {
        1: now,            # slow request — should drive the deadline
        2: now + 0.05,     # fast request — gets answered quickly
    }
    original = mcp_pipe.TOOL_CALL_TIMEOUT
    mcp_pipe.TOOL_CALL_TIMEOUT = 0.4
    try:
        async def runner():
            task = asyncio.create_task(
                mcp_pipe.pipe_process_to_websocket(proc, ws, "test", pending)
            )
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2.0)
        asyncio.run(runner())

        # Outcome: id=2 got its result (forwarded to ws), but id=1's deadline
        # expired → error response sent for id=1 + subprocess killed.
        # ws.sent should contain two messages: the result line for id=2
        # and the timeout error for id=1.
        ids_seen = [json.loads(m).get("id") for m in ws.sent]
        assert 1 in ids_seen, f"timeout error for slow request id=1 missing; ws.sent={ws.sent}"
        # Verify the id=1 message is an error (not a result)
        for m in ws.sent:
            p = json.loads(m)
            if p.get("id") == 1:
                assert "error" in p
                assert p["error"]["code"] == -32001
        assert proc.poll() is not None, "subprocess should have been killed"
    finally:
        mcp_pipe.TOOL_CALL_TIMEOUT = original
        if proc.poll() is None:
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)


def test_response_clears_pending():
    """When a JSON-RPC response flows out, its id should be removed from pending."""
    # Build a subprocess that prints a single response line then exits.
    response_line = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {}})
    proc = subprocess.Popen(
        [sys.executable, "-c", f"print({response_line!r}, flush=True); import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    ws = FakeWebSocket([])
    pending: dict = {7: time.monotonic()}

    try:
        async def runner():
            task = asyncio.create_task(
                mcp_pipe.pipe_process_to_websocket(proc, ws, "test", pending)
            )
            await asyncio.sleep(0.5)  # give it time to consume the line
            proc.kill()  # let readline thread return EOF
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task

        asyncio.run(runner())

        assert 7 not in pending, "pending should be cleared once response is forwarded"
        assert len(ws.sent) == 1
        assert json.loads(ws.sent[0])["id"] == 7
    finally:
        proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)
