"""
MCP 连接进程管理器
管理 mcp_pipe.py 子进程的生命周期
"""
import asyncio
import os
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
    error_message: Optional[str] = None
    restart_count: int = 0
    started_at: Optional[datetime] = None
    logs: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    _log_task: Optional[asyncio.Task] = None


class MCPProcessManager:
    """管理 MCP 子进程的生命周期"""

    def __init__(self):
        self.connections: Dict[str, MCPProcess] = {}
        self._monitor_task: Optional[asyncio.Task] = None

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
                                auto_start: bool = True) -> bool:
        """启动一个 MCP 连接（子进程）"""
        # 如果已经有运行中的进程，先停止
        if conn_id in self.connections:
            existing = self.connections[conn_id]
            if existing.process and existing.process.returncode is None:
                await self.stop_connection(conn_id)

        # 确定 mcp_pipe.py 路径
        mcp_pipe_path = self._get_mcp_pipe_path()
        if not mcp_pipe_path:
            logger.error("mcp_pipe.py not found")
            return False

        # 设置环境变量
        env = os.environ.copy()
        env['MCP_ENDPOINT'] = endpoint
        env['WAREHOUSE_API_KEY'] = api_key
        env['WAREHOUSE_API_URL'] = 'http://localhost:2124/api'

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, mcp_pipe_path,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.path.dirname(mcp_pipe_path)
            )

            mcp_proc = MCPProcess(
                conn_id=conn_id,
                endpoint=endpoint,
                api_key=api_key,
                process=process,
                status='running',
                started_at=datetime.now(),
                restart_count=0
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
                proc.process.terminate()
                await asyncio.wait_for(proc.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.process.kill()
                await proc.process.wait()
            except Exception as e:
                logger.error(f"Error stopping connection '{conn_id}': {e}")

        # 取消日志收集任务
        if proc._log_task and not proc._log_task.done():
            proc._log_task.cancel()

        proc.status = 'stopped'
        proc.error_message = None
        logger.info(f"MCP connection '{conn_id}' stopped")
        return True

    async def restart_connection(self, conn_id: str, endpoint: str = None,
                                  api_key: str = None) -> bool:
        """重启一个 MCP 连接（重置计数器）"""
        if conn_id in self.connections:
            proc = self.connections[conn_id]
            endpoint = endpoint or proc.endpoint
            api_key = api_key or proc.api_key
            await self.stop_connection(conn_id)

        return await self.start_connection(conn_id, endpoint, api_key)

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

        uptime = None
        if proc.started_at and proc.status == 'running':
            uptime = int((datetime.now() - proc.started_at).total_seconds())

        return {
            'status': proc.status,
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
        """收集子进程的 stdout/stderr 输出"""
        try:
            async def read_stream(stream, prefix):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode('utf-8', errors='replace').rstrip()
                    timestamp = datetime.now().strftime('%H:%M:%S')
                    proc.logs.append(f"[{timestamp}] {prefix} {text}")

            if proc.process:
                await asyncio.gather(
                    read_stream(proc.process.stdout, 'OUT'),
                    read_stream(proc.process.stderr, 'ERR')
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Log collection ended for '{proc.conn_id}': {e}")

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
            from database import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE mcp_connections
                SET status = ?, error_message = ?, restart_count = ?,
                    updated_at = ?
                WHERE id = ?
            ''', (status, error_message, restart_count,
                  datetime.now().isoformat(), conn_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to update DB status for '{conn_id}': {e}")
