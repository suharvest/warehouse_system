"""
Simple MCP stdio <-> WebSocket pipe with optional unified config.
Version: 0.2.0

Usage (env):
    export MCP_ENDPOINT=<ws_endpoint>
    # Windows (PowerShell): $env:MCP_ENDPOINT = "<ws_endpoint>"

Start server process(es) from config:
Run all configured servers (default)
    python mcp_pipe.py

Run a single local server script (back-compat)
    python mcp_pipe.py path/to/server.py

Config discovery order:
    $MCP_CONFIG, then ./mcp_config.json

Env overrides:
    (none for proxy; uses current Python: python -m mcp_proxy)
"""

import asyncio
import time
import websockets
import subprocess
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
import json
from dotenv import load_dotenv

# Auto-load environment variables from a .env file if present
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MCP_PIPE')

# File sink so docker logs misses don't blind ops
_log_dir = os.environ.get('MCP_PIPE_LOG_DIR', '/app/logs')
try:
    os.makedirs(_log_dir, exist_ok=True)
    _max_bytes = int(os.environ.get('MCP_PIPE_LOG_MAX_BYTES', str(10 * 1024 * 1024)))
    _backup_count = int(os.environ.get('MCP_PIPE_LOG_BACKUP_COUNT', '5'))
    _fh = RotatingFileHandler(
        os.path.join(_log_dir, 'mcp_pipe.log'),
        maxBytes=_max_bytes,
        backupCount=_backup_count,
    )
    _fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(_fh)
except (OSError, ValueError):
    pass

# Reconnection settings
INITIAL_BACKOFF = 1  # Initial wait time in seconds
MAX_BACKOFF = 60  # Cap reconnect interval at 60s (was 600s — 2-5 min recovery felt broken)

# Timeout settings
TOOL_CALL_TIMEOUT = 60   # Max seconds to wait for warehouse_mcp to produce one response line
WS_PING_INTERVAL = 10    # WebSocket keepalive ping interval (seconds) — shorter to survive NAT idle
WS_PING_TIMEOUT = 10     # WebSocket ping response deadline (seconds)


def _safe_label(value, limit=160):
    text = str(value or '').replace('\n', ' ').replace('\r', ' ').strip()
    return text[:limit]


def build_log_target(target):
    """Build a non-secret log label for one MCP connection."""
    fields = [
        ('conn_id', os.environ.get('MCP_LOG_CONN_ID')),
        ('name', os.environ.get('MCP_LOG_NAME')),
        ('tenant_id', os.environ.get('MCP_LOG_TENANT_ID')),
        ('tenant', os.environ.get('MCP_LOG_TENANT_NAME')),
        ('warehouse_id', os.environ.get('MCP_LOG_WAREHOUSE_ID')),
        ('warehouse', os.environ.get('MCP_LOG_WAREHOUSE_NAME')),
    ]
    parts = [f"{key}={_safe_label(value)}" for key, value in fields if _safe_label(value)]
    target_name = os.path.basename(target) if target and os.path.exists(target) else target
    if target_name:
        parts.append(f"target={_safe_label(target_name)}")
    return ' '.join(parts) or _safe_label(target) or 'mcp'


async def connect_with_retry(uri, target, log_target=None):
    """Connect to WebSocket server with retry mechanism for a given server target."""
    log_target = log_target or build_log_target(target)
    reconnect_attempt = 0
    backoff = INITIAL_BACKOFF
    while True:  # Infinite reconnection
        try:
            # First retry after a failure: immediate (transient blips recover instantly).
            # Subsequent retries: exponential backoff capped at MAX_BACKOFF.
            if reconnect_attempt > 1:
                logger.info(f"[{log_target}] Waiting {backoff}s before reconnection attempt {reconnect_attempt}...")
                await asyncio.sleep(backoff)
            elif reconnect_attempt == 1:
                logger.info(f"[{log_target}] Immediate reconnect attempt {reconnect_attempt}")

            # Attempt to connect
            await connect_to_server(uri, target, log_target)

        except Exception as e:
            reconnect_attempt += 1
            logger.warning(f"[{log_target}] Connection closed (attempt {reconnect_attempt}): {e}")
            # Calculate wait time for next reconnection (exponential backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

async def connect_to_server(uri, target, log_target=None):
    """Connect to WebSocket server and pipe stdio for the given server target."""
    log_target = log_target or build_log_target(target)
    try:
        logger.info(f"[{log_target}] Connecting to WebSocket server...")
        async with websockets.connect(
            uri,
            open_timeout=10,
            ping_interval=WS_PING_INTERVAL,
            ping_timeout=WS_PING_TIMEOUT,
            max_size=None,  # cloud pushes inventory/tool catalog > 1 MiB default → 1009 close
        ) as websocket:
            logger.info(f"[{log_target}] Successfully connected to WebSocket server")

            # Start server process (built from CLI arg or config)
            cmd, env = build_server_command(target)
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding='utf-8',
                text=True,
                env=env
            )
            logger.info(f"[{log_target}] Started server process: {' '.join(cmd)}")
            
            # Outstanding JSON-RPC requests: id -> monotonic timestamp when forwarded
            # to subprocess. Used so TOOL_CALL_TIMEOUT only fires when there's actually
            # a request waiting on a response — idle periods between cloud requests
            # must not kill the subprocess (was the cause of the ~60s reconnect cycle).
            pending_requests: dict = {}

            # Create two tasks: read from WebSocket and write to process, read from process and write to WebSocket
            await asyncio.gather(
                pipe_websocket_to_process(websocket, process, log_target, pending_requests),
                pipe_process_to_websocket(process, websocket, log_target, pending_requests),
                pipe_process_stderr_to_terminal(process, log_target)
            )
    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"[{log_target}] WebSocket connection closed: {e}")
        raise  # Re-throw exception to trigger reconnection
    except Exception as e:
        logger.error(f"[{log_target}] Connection error: {e}")
        raise  # Re-throw exception
    finally:
        # Ensure the child process is properly terminated
        if 'process' in locals():
            logger.info(f"[{log_target}] Terminating server process")
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.info(f"[{log_target}] Server process terminated")

def _classify_json_rpc(parsed):
    """Return ('request', id) | ('response', id) | ('notification', None) | (None, None).

    Per JSON-RPC 2.0: requests have method+id; notifications have method but no id;
    responses have result|error and id. We treat only requests as "needs response".
    """
    if not isinstance(parsed, dict):
        return (None, None)
    has_id = 'id' in parsed
    has_method = 'method' in parsed
    if has_method and has_id:
        return ('request', parsed['id'])
    if has_method and not has_id:
        return ('notification', None)
    if has_id and ('result' in parsed or 'error' in parsed):
        return ('response', parsed['id'])
    return (None, None)


async def pipe_websocket_to_process(websocket, process, target, pending_requests: dict):
    """Read data from WebSocket and write to process stdin.
    Records each JSON-RPC request id into pending_requests so the timeout handler
    only fires when a response is actually expected.
    """
    try:
        while True:
            # Read message from WebSocket
            message = await websocket.recv()
            logger.debug(f"[{target}] << {message[:120]}...")

            if isinstance(message, bytes):
                message = message.decode('utf-8')
            try:
                parsed = json.loads(message)
                kind, rid = _classify_json_rpc(parsed)
                if kind == 'request':
                    pending_requests[rid] = time.monotonic()
            except Exception:
                pass

            process.stdin.write(message + '\n')
            process.stdin.flush()
    except Exception as e:
        logger.error(f"[{target}] Error in WebSocket to process pipe: {e}")
        raise  # Re-throw exception to trigger reconnection
    finally:
        # Close process stdin
        if not process.stdin.closed:
            process.stdin.close()

async def pipe_process_to_websocket(process, websocket, target, pending_requests: dict):
    """Read data from process stdout and send to WebSocket.

    Timeout policy: only enforce TOOL_CALL_TIMEOUT when there's an outstanding request
    in pending_requests. When idle (no pending request), block on readline forever —
    the cloud's keepalive ping interval (~60s) used to race the readline timeout and
    kill the subprocess during idle periods, causing a reconnect storm.
    """
    try:
        while True:
            if pending_requests:
                # Per-request deadline: kill only when the *oldest* request has
                # actually been outstanding for TOOL_CALL_TIMEOUT, not when any
                # 60s gap appears in stdout. A fast ping reply doesn't reset
                # the clock on a slow tool call that's still pending.
                pending_snapshot = dict(pending_requests)  # defensive copy
                oldest_id = min(pending_snapshot, key=lambda k: pending_snapshot[k])
                deadline = pending_snapshot[oldest_id] + TOOL_CALL_TIMEOUT
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(process.stdout.readline),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    age = time.monotonic() - pending_snapshot[oldest_id]
                    logger.error(
                        f"[{target}] No response for request id={oldest_id} "
                        f"after {age:.0f}s (pending: {list(pending_snapshot)}) — "
                        f"sending error to client, then killing subprocess"
                    )
                    error_resp = json.dumps({
                        "jsonrpc": "2.0",
                        "id": oldest_id,
                        "error": {
                            "code": -32001,
                            "message": (
                                f"工具调用超时（>{TOOL_CALL_TIMEOUT}s），后端服务可能暂时繁忙。"
                                "请稍后重试。"
                            ),
                        },
                    }, ensure_ascii=False)
                    try:
                        await websocket.send(error_resp)
                    except Exception:
                        pass
                    process.kill()
                    raise  # propagates to connect_to_server → triggers reconnect
            else:
                # Idle: no outstanding request, block indefinitely
                data = await asyncio.to_thread(process.stdout.readline)

            if not data:  # If no data, the process may have ended
                logger.info(f"[{target}] Process has ended output")
                break

            # Clear pending entry when its response goes out
            try:
                parsed = json.loads(data)
                kind, rid = _classify_json_rpc(parsed)
                if kind == 'response':
                    pending_requests.pop(rid, None)
            except Exception:
                pass

            # Send data to WebSocket
            logger.debug(f"[{target}] >> {data[:120]}...")
            await websocket.send(data)
    except asyncio.TimeoutError:
        raise  # already logged, let it bubble
    except Exception as e:
        logger.error(f"[{target}] Error in process to WebSocket pipe: {e}")
        raise  # Re-throw exception to trigger reconnection

async def pipe_process_stderr_to_terminal(process, target):
    """Read data from process stderr and print to terminal"""
    try:
        while True:
            data = await asyncio.to_thread(process.stderr.readline)
            if not data:
                logger.info(f"[{target}] Process has ended stderr output")
                break
            sys.stderr.write(data)
            sys.stderr.flush()
    except asyncio.CancelledError:
        pass  # Normal shutdown when gather cancels this task
    except Exception as e:
        logger.error(f"[{target}] Error in process stderr pipe: {e}")
        raise

def signal_handler(sig, frame):
    """Handle interrupt signals"""
    logger.info("Received interrupt signal, shutting down...")
    sys.exit(0)

def load_config():
    """Load JSON config from $MCP_CONFIG or ./mcp_config.json. Return dict or {}."""
    path = os.environ.get("MCP_CONFIG") or os.path.join(os.getcwd(), "mcp_config.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config {path}: {e}")
        return {}


def build_server_command(target=None):
    """Build [cmd,...] and env for the server process for a given target.

    Priority:
    - If target matches a server in config.mcpServers: use its definition
    - Else: treat target as a Python script path (back-compat)
    If target is None, read from sys.argv[1].
    """
    if target is None:
        assert len(sys.argv) >= 2, "missing server name or script path"
        target = sys.argv[1]
    cfg = load_config()
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}

    if target in servers:
        entry = servers[target] or {}
        if entry.get("disabled"):
            raise RuntimeError(f"Server '{target}' is disabled in config")
        typ = (entry.get("type") or entry.get("transportType") or "stdio").lower()

        # environment for child process
        child_env = os.environ.copy()
        for k, v in (entry.get("env") or {}).items():
            child_env[str(k)] = str(v)

        if typ == "stdio":
            command = entry.get("command")
            args = entry.get("args") or []
            if not command:
                raise RuntimeError(f"Server '{target}' is missing 'command'")
            return [command, *args], child_env

        if typ in ("sse", "http", "streamablehttp"):
            url = entry.get("url")
            if not url:
                raise RuntimeError(f"Server '{target}' (type {typ}) is missing 'url'")
            # Unified approach: always use current Python to run mcp-proxy module
            cmd = [sys.executable, "-m", "mcp_proxy"]
            if typ in ("http", "streamablehttp"):
                cmd += ["--transport", "streamablehttp"]
            # optional headers: {"Authorization": "Bearer xxx"}
            headers = entry.get("headers") or {}
            for hk, hv in headers.items():
                cmd += ["-H", hk, str(hv)]
            cmd.append(url)
            return cmd, child_env

        raise RuntimeError(f"Unsupported server type: {typ}")

    # Fallback to script path (back-compat)
    script_path = target
    if not os.path.exists(script_path):
        raise RuntimeError(
            f"'{target}' is neither a configured server nor an existing script"
        )
    return [sys.executable, script_path], os.environ.copy()

if __name__ == "__main__":
    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)
    
    # Get token from environment variable or command line arguments
    endpoint_url = os.environ.get('MCP_ENDPOINT')
    if not endpoint_url:
        logger.error("Please set the `MCP_ENDPOINT` environment variable")
        sys.exit(1)
    
    # Determine target: default to all if no arg; single target otherwise
    target_arg = sys.argv[1] if len(sys.argv) >= 2 else None

    async def _main():
        if not target_arg:
            cfg = load_config()
            servers_cfg = (cfg.get("mcpServers") or {})
            all_servers = list(servers_cfg.keys())
            enabled = [name for name, entry in servers_cfg.items() if not (entry or {}).get("disabled")]
            skipped = [name for name in all_servers if name not in enabled]
            if skipped:
                logger.info(f"Skipping disabled servers: {', '.join(skipped)}")
            if not enabled:
                raise RuntimeError("No enabled mcpServers found in config")
            logger.info(f"Starting servers: {', '.join(enabled)}")
            tasks = [
                asyncio.create_task(connect_with_retry(endpoint_url, t, build_log_target(t)))
                for t in enabled
            ]
            # Run all forever; if any crashes it will auto-retry inside
            await asyncio.gather(*tasks)
        else:
            if os.path.exists(target_arg):
                await connect_with_retry(endpoint_url, target_arg, build_log_target(target_arg))
            else:
                logger.error("Argument must be a local Python script path. To run configured servers, run without arguments.")
                sys.exit(1)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Program execution error: {e}")
