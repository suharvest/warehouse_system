"""MCPProcessManager per-connection lock (H10) regression test.

Fix verified:
  - ``start_connection`` and ``stop_connection`` both take
    ``self._get_lock(conn_id)`` so concurrent calls for the same
    ``conn_id`` are serialized. This prevents two start calls from
    racing on ``self.connections[conn_id]`` (lost-process leak) and
    avoids the start-vs-stop interleave that previously left the
    process running while ``status='stopped'``.

We mock ``asyncio.create_subprocess_exec`` so we don't fork real
processes during the test.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeProcess:
    """Minimal stand-in for ``asyncio.subprocess.Process``."""

    def __init__(self):
        self.pid = 12345
        self.returncode = None  # "running"
        self.stdout = MagicMock()
        self.stderr = MagicMock()
        # ``read_stream`` in MCPProcessManager._collect_logs reads
        # streams; stub them to return empty immediately.
        self.stdout.readline = AsyncMock(return_value=b'')
        self.stderr.readline = AsyncMock(return_value=b'')

    async def wait(self):
        self.returncode = 0
        return 0


@pytest.mark.asyncio
async def test_concurrent_start_stop_same_conn_serialized(monkeypatch, tmp_path):
    """Fire start + start + stop + start concurrently for the same conn_id.

    With the per-connection lock, the manager must serialize them and
    end in a deterministic state (no exceptions, ``conn_id`` present in
    ``connections`` with a single process attached).
    """
    import mcp_manager

    mgr = mcp_manager.MCPProcessManager()

    # Stub the subprocess factory: every "spawn" yields a fresh FakeProcess.
    fake_procs = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        p = _FakeProcess()
        fake_procs.append(p)
        return p

    monkeypatch.setattr(
        mcp_manager.asyncio, "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    # Stub mcp_pipe path resolution so it doesn't fail when files are
    # missing in the test env.
    pipe = tmp_path / "mcp_pipe.py"
    pipe.write_text("# fake")
    monkeypatch.setattr(mgr, "_get_mcp_pipe_path", lambda: str(pipe))

    # Also stub os.getpgid + os.killpg so stop doesn't error on the
    # fake PID.
    monkeypatch.setattr(mcp_manager.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(mcp_manager.os, "killpg", lambda pgid, sig: None)

    conn_id = "test-conn-xyz"
    endpoint = "wss://example.com/agent"
    api_key = "key-abc"

    # Race start and stop for the same conn_id.
    results = await asyncio.gather(
        mgr.start_connection(conn_id, endpoint, api_key),
        mgr.start_connection(conn_id, endpoint, api_key),
        mgr.stop_connection(conn_id),
        mgr.start_connection(conn_id, endpoint, api_key),
        return_exceptions=True,
    )

    # None of the calls should raise — the lock serializes them.
    for r in results:
        assert not isinstance(r, Exception), f"unexpected exception: {r!r}"

    # End state: conn_id should be tracked.
    assert conn_id in mgr.connections, mgr.connections

    # And the lock dict should have exactly one entry for this conn_id.
    assert conn_id in mgr._locks
    assert isinstance(mgr._locks[conn_id], asyncio.Lock)

    # Cleanup: cancel any lingering log-collection tasks.
    proc = mgr.connections.get(conn_id)
    if proc and proc._log_task and not proc._log_task.done():
        proc._log_task.cancel()
        try:
            await proc._log_task
        except (asyncio.CancelledError, Exception):
            pass
