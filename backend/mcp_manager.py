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
    started_at: Optional[datetime] = None
    logs: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    _log_task: Optional[asyncio.Task] = None


class MCPProcessManager:
    """管理 MCP 子进程的生命周期"""

    def __init__(self):
        self.connections: Dict[str, MCPProcess] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        atexit.register(self._cleanup_on_exit)

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
                                auto_start: bool = True, debug_mode: bool = False) -> bool:
        """启动一个 MCP 连接（子进程）"""
        # 如果已经有运行中的进程，先停止
        if conn_id in self.connections:
            existing = self.connections[conn_id]
            if existing.process and existing.process.returncode is None:
                await self.stop_connection(conn_id)

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
                error_message=str(e)
            )
            return False

    async def stop_connection(self, conn_id: str) -> bool:
        """停止一个 MCP 连接"""
        if conn_id not in self.connections:
            return False

        proc = self.connections[conn_id]
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
                                  api_key: str = None) -> bool:
        """重启一个 MCP 连接（重置计数器，保留原 debug_mode 设置）"""
        old_debug = False
        if conn_id in self.connections:
            proc = self.connections[conn_id]
            endpoint = endpoint or proc.endpoint
            api_key = api_key or proc.api_key
            old_debug = proc.debug_mode
            await self.stop_connection(conn_id)

        return await self.start_connection(conn_id, endpoint, api_key, debug_mode=old_debug)

    async def toggle_debug(self, conn_id: str, endpoint: str, api_key: str, enable: bool) -> bool:
        """切换调试模式，重启进程使生效"""
        await self.stop_connection(conn_id)
        return await self.start_connection(conn_id, endpoint, api_key, debug_mode=enable)

    def remove_connection(self, conn_id: str):
        """从管理器中移除连接记录"""
        if conn_id in self.connections:
            del self.connections[conn_id]

    def get_connection_status(self, conn_id: str) -> dict:
        """获取连接的实时状态"""
        if conn_id not in self.connections:
            return {'status': 'stopped', 'pid': None}

        proc = self.connections[conn_id]
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
            'pid': proc.process.pid if proc.process and proc.process.returncode is None else None,
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

    async def stop_all(self):
        """停止所有连接"""
        for conn_id in list(self.connections.keys()):
            await self.stop_connection(conn_id)
        await self.stop_monitor()

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
        elif 'Successfully connected to WebSocket server' in text:
            proc.websocket_status = 'connected'
            proc.websocket_error = None
        elif 'WebSocket connection closed' in text:
            proc.websocket_status = 'disconnected'
            proc.websocket_error = text
        elif 'Connection error' in text or 'Connection closed' in text:
            proc.websocket_status = 'error'
            proc.websocket_error = text

    async def _monitor_loop(self):
        """每 30s 检查进程状态，崩溃时自动重启"""
        while True:
            try:
                await asyncio.sleep(MONITOR_INTERVAL)
                for conn_id, proc in list(self.connections.items()):
                    if proc.status != 'running':
                        continue
                    if proc.process and proc.process.returncode is not None:
                        # 进程已退出
                        logger.warning(
                            f"MCP connection '{conn_id}' exited "
                            f"(code: {proc.process.returncode})"
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
            conn_id, proc.endpoint, proc.api_key
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
