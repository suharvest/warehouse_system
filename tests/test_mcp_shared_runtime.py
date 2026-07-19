import asyncio
import json
import os
from contextlib import suppress

import pytest
import websockets


class _ProviderStub:
    def __init__(self, api_key):
        self.api_key = api_key

    def query_stock(self, product_name):
        return {
            'success': True,
            'product': {
                'name': self.api_key,
                'current_stock': 1,
                'unit': 'item',
            },
            'batches': [],
        }


class _FakeSharedRuntime:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def create_session_state(self, api_base_url, api_key, *, debug=False):
        return {
            'api_base_url': api_base_url,
            'api_key': api_key,
            'debug': debug,
        }

    async def run_connection(self, endpoint, state, log_target, callback):
        callback('connecting', 'Connecting to WebSocket server...')
        callback('connected', 'Successfully connected to WebSocket server')
        callback(
            'protocol_ready',
            'RPC server->cloud response id=0 outcome=result bytes=100',
        )
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_manager_starts_and_stops_shared_session(monkeypatch):
    import mcp_manager

    monkeypatch.setenv('MCP_SHARED_RUNTIME', '1')
    manager = mcp_manager.MCPProcessManager()
    runtime = _FakeSharedRuntime()
    manager._shared_runtime = runtime

    started = await manager.start_connection(
        'conn-shared',
        'wss://example.invalid/agent',
        'tenant-key',
        log_context={'name': 'shared-test', 'tenant_id': 7},
    )
    assert started is True
    await asyncio.sleep(0)

    status = manager.get_connection_status('conn-shared')
    assert status['status'] == 'running'
    assert status['websocket_status'] == 'connected'
    assert status['pid'] == os.getpid()
    assert await manager.wait_for_protocol_ready('conn-shared', timeout=0.1)

    assert await manager.stop_connection('conn-shared') is True
    stopped = manager.get_connection_status('conn-shared')
    assert stopped['status'] == 'stopped'
    assert stopped['pid'] is None

    await manager.stop_all()
    assert runtime.stopped is True


@pytest.mark.asyncio
async def test_monitor_restarts_fatally_stopped_shared_task(monkeypatch):
    import mcp_manager

    manager = object.__new__(mcp_manager.MCPProcessManager)
    manager.connections = {}
    failed_task = asyncio.create_task(asyncio.sleep(0))
    await failed_task
    proc = mcp_manager.MCPProcess(
        'conn-fatal',
        'wss://example.invalid/agent',
        'tenant-key',
        status='error',
        websocket_status='error',
        _bridge_task=failed_task,
    )
    manager.connections[proc.conn_id] = proc
    restarted = []

    async def stop_after_restart(conn_id):
        restarted.append(conn_id)
        raise asyncio.CancelledError

    monkeypatch.setattr(mcp_manager, 'MONITOR_INTERVAL', 0)
    monkeypatch.setattr(manager, '_auto_restart', stop_after_restart)

    await manager._monitor_loop()

    assert restarted == ['conn-fatal']


@pytest.mark.asyncio
async def test_runtime_context_isolates_provider_cache_across_threads(monkeypatch):
    from mcp_shared_runtime import _load_warehouse_mcp

    warehouse_mcp = _load_warehouse_mcp()

    loaded_keys = []

    def load_provider(config):
        key = config['auth']['key']
        loaded_keys.append(key)
        return _ProviderStub(key)

    monkeypatch.setattr(
        warehouse_mcp,
        '_load_provider_from_db_or_default',
        load_provider,
    )
    state_a = warehouse_mcp.create_runtime_state(
        'http://127.0.0.1:2125/api', 'tenant-a'
    )
    state_b = warehouse_mcp.create_runtime_state(
        'http://127.0.0.1:2125/api', 'tenant-b'
    )
    barrier = asyncio.Event()

    async def resolve(state):
        with warehouse_mcp.runtime_context(state):
            await barrier.wait()
            first = await asyncio.to_thread(warehouse_mcp._get_provider)
            second = await asyncio.to_thread(warehouse_mcp._get_provider)
            assert first is second
            return first.api_key

    tasks = [asyncio.create_task(resolve(state_a)), asyncio.create_task(resolve(state_b))]
    barrier.set()
    assert set(await asyncio.gather(*tasks)) == {'tenant-a', 'tenant-b'}
    assert sorted(loaded_keys) == ['tenant-a', 'tenant-b']


@pytest.mark.asyncio
async def test_two_websocket_sessions_do_not_cross_tenant_provider(monkeypatch):
    from mcp_shared_runtime import SharedMCPRuntime

    runtime = SharedMCPRuntime()
    await runtime.start()
    monkeypatch.setattr(
        runtime._warehouse_mcp,
        '_load_provider_from_db_or_default',
        lambda config: _ProviderStub(config['auth']['key']),
    )

    responses = []
    finished = asyncio.Event()

    async def watcher(websocket):
        await websocket.send(json.dumps({
            'jsonrpc': '2.0',
            'id': 0,
            'method': 'initialize',
            'params': {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 'isolation-test', 'version': '1.0'},
            },
        }))
        initialize_response = json.loads(await websocket.recv())
        assert initialize_response['id'] == 0
        assert 'result' in initialize_response

        await websocket.send(json.dumps({
            'jsonrpc': '2.0',
            'method': 'notifications/initialized',
            'params': {},
        }))
        await websocket.send(json.dumps({
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/list',
            'params': {},
        }))
        tools_response = json.loads(await websocket.recv())
        assert tools_response['id'] == 1

        await websocket.send(json.dumps({
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'tools/call',
            'params': {
                'name': 'query_stock',
                'arguments': {'product_name': 'widget'},
            },
        }))
        tool_response = json.loads(await websocket.recv())
        assert tool_response['id'] == 2
        responses.append(json.dumps(tool_response, ensure_ascii=False))
        if len(responses) == 2:
            finished.set()
        await finished.wait()

    server = await websockets.serve(watcher, '127.0.0.1', 0, ping_interval=None)
    port = server.sockets[0].getsockname()[1]
    endpoint = f'ws://127.0.0.1:{port}'
    states = [
        runtime.create_session_state('http://127.0.0.1:9/api', 'tenant-a'),
        runtime.create_session_state('http://127.0.0.1:9/api', 'tenant-b'),
    ]
    tasks = [
        asyncio.create_task(
            runtime.run_connection(endpoint, state, f'conn-{index}')
        )
        for index, state in enumerate(states)
    ]

    try:
        await asyncio.wait_for(finished.wait(), timeout=10)
        payload = '\n'.join(responses)
        assert 'tenant-a' in payload
        assert 'tenant-b' in payload
        assert len(responses) == 2
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        server.close()
        await server.wait_closed()
        await runtime.stop()


@pytest.mark.asyncio
async def test_shared_runtime_sends_timeout_error(monkeypatch):
    import mcp_shared_runtime

    runtime = mcp_shared_runtime.SharedMCPRuntime()
    await runtime.start()
    monkeypatch.setattr(mcp_shared_runtime, 'TOOL_CALL_TIMEOUT', 0.2)

    class _NoResponseServer:
        async def run(self, *args, **kwargs):
            await asyncio.Event().wait()

    runtime._server = _NoResponseServer()
    timeout_payload = {}
    response_received = asyncio.Event()

    async def watcher(websocket):
        await websocket.send(json.dumps({
            'jsonrpc': '2.0',
            'id': 99,
            'method': 'tools/call',
            'params': {
                'name': 'query_stock',
                'arguments': {'product_name': 'timeout'},
            },
        }))
        timeout_payload.update(json.loads(await websocket.recv()))
        response_received.set()

    server = await websockets.serve(watcher, '127.0.0.1', 0, ping_interval=None)
    port = server.sockets[0].getsockname()[1]
    state = runtime.create_session_state('http://127.0.0.1:9/api', 'tenant-timeout')

    try:
        with pytest.raises(mcp_shared_runtime.MCPToolTimeout):
            await asyncio.wait_for(
                runtime._run_session(
                    f'ws://127.0.0.1:{port}',
                    state,
                    'timeout-test',
                    None,
                ),
                timeout=3,
            )
        await asyncio.wait_for(response_received.wait(), timeout=1)
        assert timeout_payload['id'] == 99
        assert timeout_payload['error']['code'] == -32001
    finally:
        server.close()
        await server.wait_closed()
        await runtime.stop()


@pytest.mark.asyncio
async def test_shared_runtime_retries_failed_session(monkeypatch):
    import mcp_shared_runtime

    runtime = mcp_shared_runtime.SharedMCPRuntime()
    runtime._started = True
    attempts = 0
    third_attempt = asyncio.Event()
    events = []

    async def fake_start():
        return None

    async def fake_session(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(f'failure-{attempts}')
        third_attempt.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, 'start', fake_start)
    monkeypatch.setattr(runtime, '_run_session', fake_session)
    monkeypatch.setattr(mcp_shared_runtime.random, 'uniform', lambda *args: 0.001)

    task = asyncio.create_task(runtime.run_connection(
        'wss://example.invalid',
        {},
        'retry-test',
        lambda event, message: events.append((event, message)),
    ))
    try:
        await asyncio.wait_for(third_attempt.wait(), timeout=1)
        assert attempts == 3
        assert [event for event, _ in events].count('disconnected') == 2
        assert [event for event, _ in events].count('reconnecting') == 2
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
