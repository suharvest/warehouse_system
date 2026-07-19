"""
MCP 连接进程管理器
管理 mcp_pipe.py 子进程的生命周期
"""
import asyncio
import atexit
import os
import signal
import sys
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional
from datetime import datetime
from collections import deque

logger = logging.getLogger('warehouse.mcp')

MAX_RESTART_COUNT = 5
MONITOR_INTERVAL = 30  # seconds
MAX_LOG_LINES = 200
DEFAULT_AUTOSTART_STAGGER_SECONDS = 3.0
MAX_AUTOSTART_STAGGER_SECONDS = 30.0


def get_autostart_stagger_seconds() -> float:
    """Return the delay between boot-time MCP process starts.

    Small production hosts cannot import FastMCP for every configured
    connection at once and still meet the watcher's handshake deadline.
    Staggering only applies to boot-time restore; manual starts remain
    immediate.
    """
    raw_value = os.environ.get(
        'MCP_AUTOSTART_STAGGER_SECONDS',
        str(DEFAULT_AUTOSTART_STAGGER_SECONDS),
    )
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid MCP_AUTOSTART_STAGGER_SECONDS=%r; using %.1fs",
            raw_value,
            DEFAULT_AUTOSTART_STAGGER_SECONDS,
        )
        return DEFAULT_AUTOSTART_STAGGER_SECONDS
    return max(0.0, min(value, MAX_AUTOSTART_STAGGER_SECONDS))


@dataclass
class MCPProcess:
    """单个MCP连接进程的状态"""
    conn_id: str
    endpoint: str
    api_key: str
    process: Optional[asyncio.subprocess.Process] = None
    status: str = 'stopped'  # stopped | running | error
    websocket_status: str = 'not_started'  # not_started | connecting | connected | disconnected | error
    websocket_error: Optional[str] = None
    error_message: Optional[str] = None
    restart_count: int = 0
    debug_mode: bool = False
    log_context: dict = field(default_factory=dict)
    started_at: Optional[datetime] = None
    logs: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    protocol_ready: bool = False
    _log_task: Optional[asyncio.Task] = None
    _bridge_task: Optional[asyncio.Task] = None


class MCPProcessManager:
    """管理 MCP 子进程的生命周期"""

    def __init__(self):
        self.connections: Dict[str, MCPProcess] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self._shared_runtime = None
        self._shared_runtime_enabled = (
            os.environ.get('MCP_SHARED_RUNTIME', '1') != '0'
        )
        # per-connection async lock：串行化同一 conn_id 的所有 state
        # 变更（start / stop / restart / toggle_debug），避免并发
        # /start + /stop 把 self.connections[conn_id] 改成不一致状态
        # （codex audit ad0265a253981469c HIGH）。
        self._locks: Dict[str, asyncio.Lock] = {}
        atexit.register(self._cleanup_on_exit)
        # Kill any orphan mcp_pipe.py from a previous backend run before we
        # spawn fresh ones. Without this, uvicorn reload / backend crash
        # leaves the old pipe alive as a PID-1 orphan, the new backend
        # spawns a second copy, and the cloud sees two clients with the
        # same identity — observed as "云端连接失败" + multi-minute recovery.
        self._kill_orphan_pipes()

    async def _get_shared_runtime(self):
        if self._shared_runtime is None:
            from mcp_shared_runtime import SharedMCPRuntime

            self._shared_runtime = SharedMCPRuntime()
        await self._shared_runtime.start()
        return self._shared_runtime

    @staticmethod
    def _kill_orphan_pipes():
        """SIGKILL any leftover mcp_pipe.py process groups not owned by this run."""
        import subprocess
        my_pid = os.getpid()
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'mcp_pipe.py'],
                capture_output=True, text=True, timeout=5,
            )
            pid_tokens = result.stdout.split()
        except FileNotFoundError:
            pid_tokens = MCPProcessManager._scan_mcp_pipe_pids_from_proc(my_pid)
            if pid_tokens is None:
                logger.warning("Orphan mcp_pipe.py scan skipped: pgrep missing and /proc unavailable")
                return
        except subprocess.TimeoutExpired as e:
            logger.warning(f"Orphan mcp_pipe.py scan skipped: {e}")
            return
        killed = []
        for token in pid_tokens:
            if not token.strip().isdigit():
                continue
            pid = int(token)
            if pid == my_pid:
                continue
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
                killed.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
        if killed:
            logger.warning(f"Killed orphan mcp_pipe.py PIDs from previous run: {killed}")

    @staticmethod
    def _scan_mcp_pipe_pids_from_proc(my_pid: int) -> list[str] | None:
        """Fallback for slim images without procps/pgrep."""
        proc_dir = "/proc"
        if not os.path.isdir(proc_dir):
            return None

        pids: list[str] = []
        for name in os.listdir(proc_dir):
            if not name.isdigit() or int(name) == my_pid:
                continue
            cmdline_path = os.path.join(proc_dir, name, "cmdline")
            try:
                with open(cmdline_path, "rb") as fh:
                    cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
            except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
                continue
            if "mcp_pipe.py" in cmdline:
                pids.append(name)
        return pids

    def _get_lock(self, conn_id: str) -> asyncio.Lock:
        """懒创建 per-connection 锁。"""
        if conn_id not in self._locks:
            self._locks[conn_id] = asyncio.Lock()
        return self._locks[conn_id]

    async def start_monitor(self):
        """启动后台监控任务"""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info("MCP process monitor started")

    async def stop_monitor(self):
        """停止后台监控任务"""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            logger.info("MCP process monitor stopped")

    async def start_connection(self, conn_id: str, endpoint: str, api_key: str,
                                auto_start: bool = True, debug_mode: bool = False,
                                log_context: Optional[dict] = None) -> bool:
        """启动一个 MCP 连接（子进程）。串行化同 conn_id 的状态变更。"""
        async with self._get_lock(conn_id):
            return await self._start_connection_locked(
                conn_id, endpoint, api_key, auto_start, debug_mode, log_context
            )

    async def _start_connection_locked(self, conn_id: str, endpoint: str, api_key: str,
                                        auto_start: bool = True, debug_mode: bool = False,
                                        log_context: Optional[dict] = None) -> bool:
        """start_connection 的内部实现，**调用者必须已持有 self._get_lock(conn_id)**。
        给 restart / toggle_debug 复用以避免锁重入死锁。"""
        # 如果已经有运行中的进程，先停止（同样 unlocked，避免锁重入）
        if conn_id in self.connections:
            existing = self.connections[conn_id]
            shared_running = (
                existing._bridge_task is not None
                and not existing._bridge_task.done()
            )
            process_running = (
                existing.process is not None
                and existing.process.returncode is None
            )
            if shared_running or process_running:
                await self._stop_connection_locked(conn_id)

        # 确定 mcp_pipe.py 和 warehouse_mcp.py 路径
        mcp_pipe_path = self._get_mcp_pipe_path()
        if not mcp_pipe_path:
            logger.error("mcp_pipe.py not found")
            return False

        mcp_dir = os.path.dirname(mcp_pipe_path)
        warehouse_mcp_path = os.path.join(mcp_dir, 'warehouse_mcp.py')

        # 设置环境变量
        env = os.environ.copy()
        env['MCP_ENDPOINT'] = endpoint
        env['WAREHOUSE_API_KEY'] = api_key
        port = os.environ.get('PORT', '2124')
        env['WAREHOUSE_API_URL'] = f'http://localhost:{port}/api'
        effective_debug = debug_mode or os.environ.get('MCP_DEBUG') == '1'
        env['MCP_DEBUG'] = '1' if effective_debug else '0'
        log_context = self._normalize_log_context(
            conn_id, log_context or self._load_log_context(conn_id)
        )
        for key, value in log_context.items():
            env[f"MCP_LOG_{key.upper()}"] = str(value)

        if self._shared_runtime_enabled:
            return await self._start_shared_connection(
                conn_id=conn_id,
                endpoint=endpoint,
                api_key=api_key,
                debug_mode=effective_debug,
                log_context=log_context,
                api_url=env['WAREHOUSE_API_URL'],
            )

        try:
            # 传 warehouse_mcp.py 路径作为参数，避免依赖 mcp_config.json 的硬编码路径
            process = await asyncio.create_subprocess_exec(
                sys.executable, mcp_pipe_path, warehouse_mcp_path,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=mcp_dir,
                start_new_session=True,
            )

            mcp_proc = MCPProcess(
                conn_id=conn_id,
                endpoint=endpoint,
                api_key=api_key,
                process=process,
                status='running',
                websocket_status='connecting',
                started_at=datetime.now(),
                restart_count=0,
                debug_mode=effective_debug,
                log_context=log_context,
            )
            self.connections[conn_id] = mcp_proc

            # 启动日志收集任务
            mcp_proc._log_task = asyncio.create_task(
                self._collect_logs(mcp_proc)
            )

            logger.info(f"MCP connection '{conn_id}' started (PID: {process.pid})")
            return True

        except Exception as e:
            logger.error(f"Failed to start MCP connection '{conn_id}': {e}")
            self.connections[conn_id] = MCPProcess(
                conn_id=conn_id,
                endpoint=endpoint,
                api_key=api_key,
                status='error',
                websocket_status='error',
                websocket_error=str(e),
                error_message=str(e),
                log_context=log_context,
            )
            return False

    async def _start_shared_connection(
        self,
        *,
        conn_id: str,
        endpoint: str,
        api_key: str,
        debug_mode: bool,
        log_context: dict,
        api_url: str,
    ) -> bool:
        """Start one watcher session on the process-wide FastMCP runtime."""
        try:
            runtime = await self._get_shared_runtime()
            session_state = runtime.create_session_state(
                api_url,
                api_key,
                debug=debug_mode,
            )
            mcp_proc = MCPProcess(
                conn_id=conn_id,
                endpoint=endpoint,
                api_key=api_key,
                status='running',
                websocket_status='connecting',
                started_at=datetime.now(),
                restart_count=0,
                debug_mode=debug_mode,
                log_context=log_context,
            )
            self.connections[conn_id] = mcp_proc
            log_target = self._build_shared_log_target(log_context)

            def event_callback(event: str, message: str):
                if self.connections.get(conn_id) is not mcp_proc:
                    return
                timestamp = datetime.now().strftime('%H:%M:%S')
                mcp_proc.logs.append(f"[{timestamp}] {event.upper()} {message}")
                self._update_shared_runtime_status(mcp_proc, event, message)

            task = asyncio.create_task(
                runtime.run_connection(
                    endpoint,
                    session_state,
                    log_target,
                    event_callback,
                ),
                name=f"mcp-shared-{conn_id}",
            )
            mcp_proc._bridge_task = task
            task.add_done_callback(
                lambda completed: self._shared_task_done(mcp_proc, completed)
            )
            logger.info(
                "MCP connection '%s' started on shared runtime (PID: %s)",
                conn_id,
                os.getpid(),
            )
            return True
        except Exception as exc:
            logger.error("Failed to start shared MCP connection '%s': %s", conn_id, exc)
            self.connections[conn_id] = MCPProcess(
                conn_id=conn_id,
                endpoint=endpoint,
                api_key=api_key,
                status='error',
                websocket_status='error',
                websocket_error=str(exc),
                error_message=str(exc),
                debug_mode=debug_mode,
                log_context=log_context,
            )
            return False

    @staticmethod
    def _build_shared_log_target(log_context: dict) -> str:
        labels = (
            ('conn_id', 'conn_id'),
            ('name', 'name'),
            ('tenant_id', 'tenant_id'),
            ('tenant', 'tenant_name'),
            ('warehouse_id', 'warehouse_id'),
            ('warehouse', 'warehouse_name'),
        )
        parts = []
        for label, key in labels:
            value = log_context.get(key)
            if value is not None and str(value).strip():
                clean_value = (
                    str(value)
                    .replace('\n', ' ')
                    .replace('\r', ' ')
                    .strip()[:160]
                )
                if clean_value:
                    parts.append(f"{label}={clean_value}")
        parts.append('target=shared-fastmcp')
        return ' '.join(parts)

    @staticmethod
    def _update_shared_runtime_status(
        proc: MCPProcess,
        event: str,
        message: str,
    ):
        if event in ('connecting', 'reconnecting'):
            proc.websocket_status = 'connecting'
            proc.websocket_error = None
            proc.protocol_ready = False
        elif event == 'connected':
            proc.websocket_status = 'connected'
            proc.websocket_error = None
        elif event == 'protocol_ready':
            proc.websocket_status = 'connected'
            proc.websocket_error = None
            proc.protocol_ready = True
        elif event == 'disconnected':
            proc.websocket_status = 'disconnected'
            proc.websocket_error = message
            proc.protocol_ready = False
        elif event == 'error':
            proc.websocket_status = 'error'
            proc.websocket_error = message
            proc.protocol_ready = False

    def _shared_task_done(self, proc: MCPProcess, task: asyncio.Task):
        if task.cancelled() or proc.status != 'running':
            return
        exception = task.exception()
        proc.status = 'error'
        proc.websocket_status = 'error'
        proc.protocol_ready = False
        proc.error_message = str(exception or 'Shared MCP runtime task stopped')
        proc.websocket_error = proc.error_message
        logger.error(
            "Shared MCP connection '%s' stopped unexpectedly: %s",
            proc.conn_id,
            proc.error_message,
        )

    @staticmethod
    def _normalize_log_context(conn_id: str, context: Optional[dict]) -> dict:
        """Build safe, non-secret context forwarded to mcp_pipe logs."""
        context = dict(context or {})
        context.setdefault('conn_id', conn_id)
        allowed = (
            'conn_id', 'name', 'tenant_id', 'tenant_name',
            'warehouse_id', 'warehouse_name',
        )
        clean = {}
        for key in allowed:
            value = context.get(key)
            if value is None:
                continue
            text = str(value).replace('\n', ' ').replace('\r', ' ').strip()
            if text:
                clean[key] = text[:160]
        return clean

    @staticmethod
    def _load_log_context(conn_id: str) -> dict:
        """Load connection labels for logs without exposing endpoint/API secrets."""
        try:
            from db import get_engine
            from metadata import (
                mcp_connections as _t_mcp,
                tenants as _t_tenants,
                warehouses as _t_warehouses,
            )
            from sqlalchemy import select as _sa_select

            stmt = (
                _sa_select(
                    _t_mcp.c.id.label('conn_id'),
                    _t_mcp.c.name,
                    _t_mcp.c.tenant_id,
                    _t_tenants.c.name.label('tenant_name'),
                    _t_mcp.c.warehouse_id,
                    _t_warehouses.c.name.label('warehouse_name'),
                )
                .select_from(
                    _t_mcp
                    .outerjoin(_t_tenants, _t_mcp.c.tenant_id == _t_tenants.c.id)
                    .outerjoin(_t_warehouses, _t_mcp.c.warehouse_id == _t_warehouses.c.id)
                )
                .where(_t_mcp.c.id == conn_id)
            )
            with get_engine().connect() as conn:
                row = conn.execute(stmt).first()
            return dict(row._mapping) if row else {}
        except Exception as e:
            logger.debug(f"Failed to load MCP log context for '{conn_id}': {e}")
            return {}

    async def stop_connection(self, conn_id: str) -> bool:
        """停止一个 MCP 连接。串行化同 conn_id 的状态变更。"""
        async with self._get_lock(conn_id):
            return await self._stop_connection_locked(conn_id)

    async def _stop_connection_locked(self, conn_id: str) -> bool:
        """stop_connection 的内部实现，**调用者必须已持有 self._get_lock(conn_id)**。"""
        if conn_id not in self.connections:
            return False

        proc = self.connections[conn_id]
        if proc._bridge_task is not None:
            task = proc._bridge_task
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            proc._bridge_task = None

        if proc.process and proc.process.returncode is None:
            try:
                # 杀整个进程组（mcp_pipe + warehouse_mcp 子进程）
                pgid = os.getpgid(proc.process.pid)
                os.killpg(pgid, signal.SIGTERM)
                await asyncio.wait_for(proc.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.process.wait()
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.error(f"Error stopping connection '{conn_id}': {e}")

        # 取消日志收集任务
        if proc._log_task and not proc._log_task.done():
            proc._log_task.cancel()

        proc.status = 'stopped'
        proc.websocket_status = 'not_started'
        proc.websocket_error = None
        proc.error_message = None
        logger.info(f"MCP connection '{conn_id}' stopped")
        return True

    async def restart_connection(self, conn_id: str, endpoint: str = None,
                                  api_key: str = None,
                                  log_context: Optional[dict] = None) -> bool:
        """重启一个 MCP 连接（重置计数器，保留原 debug_mode 设置）。一次性持锁
        覆盖 stop + start，避免别人插队改状态。"""
        async with self._get_lock(conn_id):
            old_debug = False
            old_log_context = log_context
            if conn_id in self.connections:
                proc = self.connections[conn_id]
                endpoint = endpoint or proc.endpoint
                api_key = api_key or proc.api_key
                old_debug = proc.debug_mode
                old_log_context = old_log_context or proc.log_context
                await self._stop_connection_locked(conn_id)

            return await self._start_connection_locked(
                conn_id, endpoint, api_key, debug_mode=old_debug,
                log_context=old_log_context,
            )

    async def toggle_debug(self, conn_id: str, endpoint: str, api_key: str, enable: bool) -> bool:
        """切换调试模式，重启进程使生效。一次性持锁覆盖 stop + start。"""
        async with self._get_lock(conn_id):
            await self._stop_connection_locked(conn_id)
            return await self._start_connection_locked(
                conn_id, endpoint, api_key, debug_mode=enable
            )

    def remove_connection(self, conn_id: str):
        """从管理器中移除连接记录。

        调用方应当先 `await stop_connection(conn_id)`（已持过锁串行化），再
        同步调本方法。dict del 是 CPython GIL 下的原子操作，无需异步锁。
        """
        if conn_id in self.connections:
            del self.connections[conn_id]
        # 清理 lock 释放内存（lock 已不再被任何 coroutine 持有）
        self._locks.pop(conn_id, None)

    def get_connection_status(self, conn_id: str) -> dict:
        """获取连接的实时状态"""
        if conn_id not in self.connections:
            return {'status': 'stopped', 'pid': None}

        proc = self.connections[conn_id]
        if proc._bridge_task is not None and proc._bridge_task.done():
            if proc.status == 'running' and not proc._bridge_task.cancelled():
                proc.status = 'error'
                exception = proc._bridge_task.exception()
                proc.error_message = str(
                    exception or 'Shared MCP runtime task stopped'
                )
                proc.websocket_status = 'error'
                proc.websocket_error = proc.error_message
                proc.protocol_ready = False

        # 检查进程是否仍在运行
        if proc.process and proc.process.returncode is not None:
            if proc.status == 'running':
                proc.status = 'error'
                proc.error_message = f'Process exited with code {proc.process.returncode}'
                if proc.websocket_status in ('connecting', 'connected'):
                    proc.websocket_status = 'disconnected'
                    proc.websocket_error = proc.error_message

        uptime = None
        if proc.started_at and proc.status == 'running':
            uptime = int((datetime.now() - proc.started_at).total_seconds())

        return {
            'status': proc.status,
            'websocket_status': proc.websocket_status,
            'websocket_error': proc.websocket_error,
            'pid': (
                os.getpid()
                if proc._bridge_task is not None and not proc._bridge_task.done()
                else (
                    proc.process.pid
                    if proc.process and proc.process.returncode is None
                    else None
                )
            ),
            'error_message': proc.error_message,
            'restart_count': proc.restart_count,
            'uptime_seconds': uptime
        }

    def get_logs(self, conn_id: str, lines: int = 50) -> list:
        """获取连接的最近日志"""
        if conn_id not in self.connections:
            return []
        proc = self.connections[conn_id]
        logs = list(proc.logs)
        return logs[-lines:] if len(logs) > lines else logs

    async def wait_for_protocol_ready(self, conn_id: str, timeout: float = 30.0) -> bool:
        """Wait until the local MCP server answers the initialize request."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            proc = self.connections.get(conn_id)
            if proc is None or proc.status != 'running':
                return False
            if proc.protocol_ready:
                return True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.2, remaining))

    async def stop_all(self):
        """停止所有连接"""
        for conn_id in list(self.connections.keys()):
            await self.stop_connection(conn_id)
        await self.stop_monitor()
        if self._shared_runtime is not None:
            await self._shared_runtime.stop()
            self._shared_runtime = None

    def _cleanup_on_exit(self):
        """atexit 兜底：同步杀掉所有残留子进程组"""
        for proc in self.connections.values():
            if proc.process and proc.process.returncode is None:
                try:
                    pgid = os.getpgid(proc.process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    def _get_mcp_pipe_path(self) -> Optional[str]:
        """获取 mcp_pipe.py 的路径"""
        # Docker 环境: /app/mcp/mcp_pipe.py
        docker_path = '/app/mcp/mcp_pipe.py'
        if os.path.exists(docker_path):
            return docker_path

        # 开发环境: 相对于项目根目录
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dev_path = os.path.join(project_root, 'mcp', 'mcp_pipe.py')
        if os.path.exists(dev_path):
            return dev_path

        return None

    async def _collect_logs(self, proc: MCPProcess):
        """收集子进程的 stdout/stderr 输出。MCP_DEBUG=1 时同步转发到 logger。"""
        _mcp_debug = proc.debug_mode
        try:
            async def read_stream(stream, prefix):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode('utf-8', errors='replace').rstrip()
                    timestamp = datetime.now().strftime('%H:%M:%S')
                    proc.logs.append(f"[{timestamp}] {prefix} {text}")
                    self._update_websocket_status_from_log(proc, text)
                    if _mcp_debug and prefix == 'ERR':
                        logger.info(f"[mcp:{proc.conn_id}] {text}")

            if proc.process:
                await asyncio.gather(
                    read_stream(proc.process.stdout, 'OUT'),
                    read_stream(proc.process.stderr, 'ERR')
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Log collection ended for '{proc.conn_id}': {e}")

    def _update_websocket_status_from_log(self, proc: MCPProcess, text: str):
        """从 mcp_pipe 日志中提取 WebSocket 连接状态。"""
        if 'Connecting to WebSocket server' in text:
            proc.websocket_status = 'connecting'
            proc.websocket_error = None
            proc.protocol_ready = False
        elif 'Successfully connected to WebSocket server' in text:
            proc.websocket_status = 'connected'
            proc.websocket_error = None
        elif 'RPC server->cloud response id=0 outcome=result' in text:
            proc.websocket_status = 'connected'
            proc.websocket_error = None
            proc.protocol_ready = True
        elif 'WebSocket connection closed' in text:
            proc.websocket_status = 'disconnected'
            proc.websocket_error = text
            proc.protocol_ready = False
        elif 'Connection error' in text or 'Connection closed' in text:
            proc.websocket_status = 'error'
            proc.websocket_error = text
            proc.protocol_ready = False

    async def _monitor_loop(self):
        """每 30s 检查进程状态，崩溃时自动重启"""
        while True:
            try:
                await asyncio.sleep(MONITOR_INTERVAL)
                for conn_id, proc in list(self.connections.items()):
                    shared_stopped = (
                        proc._bridge_task is not None
                        and proc._bridge_task.done()
                        and not proc._bridge_task.cancelled()
                    )
                    process_stopped = (
                        proc.process is not None
                        and proc.process.returncode is not None
                    )
                    # A shared task's done callback records a visible error
                    # immediately. It still needs the monitor to restart it;
                    # stopped sessions have their task reference cleared.
                    if proc.status not in ('running', 'error'):
                        continue
                    if proc.status == 'error' and not shared_stopped:
                        continue
                    if shared_stopped or process_stopped:
                        # 进程已退出
                        logger.warning(
                            "MCP connection '%s' exited (code: %s)",
                            conn_id,
                            proc.process.returncode if proc.process else 'shared-task',
                        )
                        await self._auto_restart(conn_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

    async def _auto_restart(self, conn_id: str):
        """自动重启崩溃的连接"""
        if conn_id not in self.connections:
            return

        # If the DB record was deleted externally, clean up and don't restart
        try:
            from db import get_engine
            from metadata import mcp_connections as _t_mcp
            from sqlalchemy import select as _sa_select
            with get_engine().connect() as _conn:
                exists = _conn.execute(
                    _sa_select(_t_mcp.c.id).where(_t_mcp.c.id == conn_id)
                ).first()
            if not exists:
                logger.info(f"MCP connection '{conn_id}' no longer in DB, removing from manager")
                self.remove_connection(conn_id)
                return
        except Exception as e:
            logger.error(f"Failed to check DB for '{conn_id}': {e}")

        proc = self.connections[conn_id]
        proc.restart_count += 1

        if proc.restart_count > MAX_RESTART_COUNT:
            proc.status = 'error'
            proc.error_message = f'Max restart attempts ({MAX_RESTART_COUNT}) exceeded'
            logger.error(f"MCP connection '{conn_id}' exceeded max restarts")
            # 更新数据库状态
            self._update_db_status(conn_id, 'error', proc.error_message, proc.restart_count)
            return

        logger.info(
            f"Auto-restarting MCP connection '{conn_id}' "
            f"(attempt {proc.restart_count}/{MAX_RESTART_COUNT})"
        )

        success = await self.start_connection(
            conn_id, proc.endpoint, proc.api_key,
            debug_mode=proc.debug_mode,
            log_context=proc.log_context,
        )
        if success:
            # 保留 restart_count
            self.connections[conn_id].restart_count = proc.restart_count
            self._update_db_status(conn_id, 'running', None, proc.restart_count)
        else:
            self._update_db_status(conn_id, 'error',
                                   self.connections[conn_id].error_message,
                                   proc.restart_count)

    def _update_db_status(self, conn_id: str, status: str,
                          error_message: Optional[str], restart_count: int):
        """更新数据库中的连接状态"""
        try:
            from db import get_engine
            from metadata import mcp_connections as _t_mcp
            from sqlalchemy import update as _sa_update
            with get_engine().begin() as conn:
                conn.execute(
                    _sa_update(_t_mcp)
                    .where(_t_mcp.c.id == conn_id)
                    .values(
                        status=status,
                        error_message=error_message,
                        restart_count=restart_count,
                        updated_at=datetime.now().isoformat(),
                    )
                )
        except Exception as e:
            logger.error(f"Failed to update DB status for '{conn_id}': {e}")
