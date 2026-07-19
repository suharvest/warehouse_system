"""Shared FastMCP runtime for all cloud watcher connections.

Each WebSocket gets an independent MCP session and a ContextVar-backed tenant
configuration, while the FastMCP/Pydantic runtime is imported only once.
"""

import asyncio
import json
import logging
import os
import random
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Callable, Optional

import anyio
import websockets
from mcp import types
from mcp.server.lowlevel.server import NotificationOptions
from mcp.shared.message import SessionMessage


logger = logging.getLogger('warehouse.mcp.runtime')
logger.setLevel(logging.INFO)

_log_file_handler = None
_log_dir = os.environ.get('MCP_PIPE_LOG_DIR', '/app/logs')
try:
    os.makedirs(_log_dir, exist_ok=True)
    _log_file_handler = RotatingFileHandler(
        os.path.join(_log_dir, 'mcp_pipe.log'),
        maxBytes=int(os.environ.get('MCP_PIPE_LOG_MAX_BYTES', str(10 * 1024 * 1024))),
        backupCount=int(os.environ.get('MCP_PIPE_LOG_BACKUP_COUNT', '5')),
    )
    _log_file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(_log_file_handler)
except (OSError, ValueError):
    _log_file_handler = None

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
TOOL_CALL_TIMEOUT = 60.0
WS_PING_INTERVAL = 10
WS_PING_TIMEOUT = 10

RuntimeEventCallback = Callable[[str, str], None]


def _load_warehouse_mcp():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mcp_dir = os.path.join(project_root, 'mcp')
    if mcp_dir not in sys.path:
        sys.path.insert(0, mcp_dir)
    import warehouse_mcp  # noqa: PLC0415

    return warehouse_mcp


def _safe_label(value, limit=160):
    text = str('' if value is None else value)
    return text.replace('\n', ' ').replace('\r', ' ').strip()[:limit]


def _classify_json_rpc(parsed):
    if not isinstance(parsed, dict):
        return None, None
    if 'method' in parsed:
        return ('request', parsed.get('id')) if 'id' in parsed else ('notification', None)
    if 'id' in parsed and ('result' in parsed or 'error' in parsed):
        return 'response', parsed['id']
    return None, None


def _json_rpc_summary(parsed, byte_count):
    kind, request_id = _classify_json_rpc(parsed)
    if kind in ('request', 'notification'):
        method = _safe_label(parsed.get('method'), 100) or '?'
        return f"{kind} method={method} id={_safe_label(request_id)} bytes={byte_count}"
    if kind == 'response':
        outcome = 'error' if 'error' in parsed else 'result'
        return f"response id={_safe_label(request_id)} outcome={outcome} bytes={byte_count}"
    return f"non-json-rpc bytes={byte_count}"


class MCPToolTimeout(RuntimeError):
    pass


class SharedMCPRuntime:
    """Own one FastMCP server and run many isolated protocol sessions on it."""

    def __init__(self):
        self._warehouse_mcp = None
        self._server = None
        self._initialization_options = None
        self._lifespan_cm = None
        self._start_lock = asyncio.Lock()
        self._started = False

    async def start(self):
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            self._warehouse_mcp = _load_warehouse_mcp()
            if (
                _log_file_handler is not None
                and _log_file_handler not in self._warehouse_mcp.logger.handlers
            ):
                self._warehouse_mcp.logger.addHandler(_log_file_handler)
                self._warehouse_mcp.logger.setLevel(logging.INFO)
            self._server = self._warehouse_mcp.mcp._mcp_server
            self._initialization_options = self._server.create_initialization_options(
                NotificationOptions(tools_changed=True)
            )
            self._lifespan_cm = self._warehouse_mcp.mcp._lifespan_manager()
            await self._lifespan_cm.__aenter__()
            self._started = True
            logger.info("Shared FastMCP runtime started")

    async def stop(self):
        if not self._started:
            return
        lifespan_cm = self._lifespan_cm
        self._started = False
        self._lifespan_cm = None
        if lifespan_cm is not None:
            await lifespan_cm.__aexit__(None, None, None)
        logger.info("Shared FastMCP runtime stopped")

    def create_session_state(
        self,
        api_base_url: str,
        api_key: str,
        *,
        debug: bool = False,
    ) -> dict:
        if self._warehouse_mcp is None:
            raise RuntimeError("Shared MCP runtime has not started")
        return self._warehouse_mcp.create_runtime_state(
            api_base_url,
            api_key,
            debug=debug,
        )

    async def run_connection(
        self,
        endpoint: str,
        session_state: dict,
        log_target: str,
        event_callback: Optional[RuntimeEventCallback] = None,
    ):
        await self.start()
        reconnect_attempt = 0
        backoff = INITIAL_BACKOFF
        while True:
            try:
                if reconnect_attempt:
                    delay = min(MAX_BACKOFF, random.uniform(backoff * 0.5, backoff * 1.5))
                    message = (
                        f"Waiting {delay:.1f}s before reconnection "
                        f"attempt {reconnect_attempt}..."
                    )
                    self._emit(log_target, event_callback, 'reconnecting', message)
                    await asyncio.sleep(delay)
                await self._run_session(
                    endpoint,
                    session_state,
                    log_target,
                    event_callback,
                )
                raise RuntimeError("MCP session ended unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reconnect_attempt += 1
                message = f"Connection closed (attempt {reconnect_attempt}): {exc}"
                self._emit(log_target, event_callback, 'disconnected', message, warning=True)
                backoff = min(backoff * 2, MAX_BACKOFF)

    async def _run_session(
        self,
        endpoint: str,
        session_state: dict,
        log_target: str,
        event_callback: Optional[RuntimeEventCallback],
    ):
        self._emit(
            log_target,
            event_callback,
            'connecting',
            'Connecting to WebSocket server...',
        )
        async with websockets.connect(
            endpoint,
            open_timeout=10,
            ping_interval=WS_PING_INTERVAL,
            ping_timeout=WS_PING_TIMEOUT,
            max_size=None,
        ) as websocket:
            self._emit(
                log_target,
                event_callback,
                'connected',
                'Successfully connected to WebSocket server',
            )

            incoming_send, incoming_receive = anyio.create_memory_object_stream(0)
            outgoing_send, outgoing_receive = anyio.create_memory_object_stream(0)
            pending_requests = {}
            protocol_trace = {'last_cloud': None, 'last_server': None}

            async def websocket_to_mcp():
                async with incoming_send:
                    async for raw_message in websocket:
                        if isinstance(raw_message, bytes):
                            raw_message = raw_message.decode('utf-8')
                        parsed = json.loads(raw_message)
                        kind, request_id = _classify_json_rpc(parsed)
                        summary = _json_rpc_summary(
                            parsed, len(raw_message.encode('utf-8'))
                        )
                        protocol_trace['last_cloud'] = summary
                        self._emit(log_target, event_callback, 'protocol', f"RPC cloud->server {summary}")
                        if kind == 'request':
                            pending_requests[request_id] = time.monotonic()
                        message = types.JSONRPCMessage.model_validate_json(raw_message)
                        await incoming_send.send(SessionMessage(message))

            async def mcp_to_websocket():
                async with outgoing_receive:
                    async for session_message in outgoing_receive:
                        raw_message = session_message.message.model_dump_json(
                            by_alias=True,
                            exclude_none=True,
                        )
                        parsed = json.loads(raw_message)
                        kind, request_id = _classify_json_rpc(parsed)
                        summary = _json_rpc_summary(
                            parsed, len(raw_message.encode('utf-8'))
                        )
                        protocol_trace['last_server'] = summary
                        self._emit(log_target, event_callback, 'protocol', f"RPC server->cloud {summary}")
                        if kind == 'response':
                            pending_requests.pop(request_id, None)
                            if request_id == 0 and 'error' not in parsed:
                                self._emit(
                                    log_target,
                                    event_callback,
                                    'protocol_ready',
                                    f"RPC server->cloud {summary}",
                                )
                        await websocket.send(raw_message)

            async def timeout_watchdog():
                while True:
                    await asyncio.sleep(0.2)
                    if not pending_requests:
                        continue
                    request_id = min(
                        pending_requests,
                        key=lambda item: pending_requests[item],
                    )
                    age = time.monotonic() - pending_requests[request_id]
                    if age < TOOL_CALL_TIMEOUT:
                        continue
                    error_response = json.dumps({
                        'jsonrpc': '2.0',
                        'id': request_id,
                        'error': {
                            'code': -32001,
                            'message': (
                                f"工具调用超时（>{int(TOOL_CALL_TIMEOUT)}s），"
                                "后端服务可能暂时繁忙。请稍后重试。"
                            ),
                        },
                    }, ensure_ascii=False)
                    await websocket.send(error_response)
                    raise MCPToolTimeout(
                        f"No response for request id={request_id} after {age:.0f}s"
                    )

            with self._warehouse_mcp.runtime_context(session_state):
                tasks = [
                    asyncio.create_task(websocket_to_mcp()),
                    asyncio.create_task(mcp_to_websocket()),
                    asyncio.create_task(timeout_watchdog()),
                    asyncio.create_task(self._server.run(
                        incoming_receive,
                        outgoing_send,
                        self._initialization_options,
                    )),
                ]
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    exception = task.exception()
                    if exception is not None:
                        raise exception

                trace = (
                    f"last_cloud={protocol_trace['last_cloud'] or 'none'}; "
                    f"last_server={protocol_trace['last_server'] or 'none'}"
                )
                raise RuntimeError(f"MCP session stream ended; {trace}")

    @staticmethod
    def _emit(
        log_target: str,
        callback: Optional[RuntimeEventCallback],
        event: str,
        message: str,
        *,
        warning: bool = False,
    ):
        log_method = logger.warning if warning else logger.info
        log_method("[%s] %s", log_target, message)
        if callback is None:
            return
        try:
            callback(event, message)
        except Exception as exc:
            logger.debug("MCP runtime event callback failed: %s", exc)
