"""warehouse MCP 语音 E2E 测试基础设施。

本套件属于 warehouse_system，用于回归 warehouse 的 MCP 功能。
py-xiaozhi 只是驱动"虚拟小智设备"的**脚手架**（外部运行时依赖），
通过 PY_XIAOZHI_ROOT 引用，不在本仓库内。

运行方式（必须用 py-xiaozhi 的 venv，因为要 in-process 驱动它）：
    PY_XIAOZHI_ROOT=/Users/harvest/project/py-xiaozhi \
    /Users/harvest/project/py-xiaozhi/.venv/bin/python -m pytest \
        e2e_voice_mcp/ -c e2e_voice_mcp/pytest.ini

前置条件见 README.md。
"""

import asyncio
import contextlib
import os
import pathlib
import sys

os.environ.setdefault("XIAOZHI_DISABLE_AUDIO", "1")

# py-xiaozhi 脚手架根目录：默认用 vendor 下的 git submodule；PY_XIAOZHI_ROOT 可覆盖
_E2E_DIR = pathlib.Path(__file__).resolve().parent
_DEFAULT_ROOT = _E2E_DIR / "vendor" / "py-xiaozhi"
_XIAOZHI_ROOT = pathlib.Path(
    os.environ.get("PY_XIAOZHI_ROOT", str(_DEFAULT_ROOT))
).resolve()

if not (_XIAOZHI_ROOT / "src").is_dir():
    raise RuntimeError(
        f"找不到 py-xiaozhi 脚手架于 {_XIAOZHI_ROOT}。"
        "先 `git submodule update --init e2e_voice_mcp/vendor/py-xiaozhi` "
        "再 `cd e2e_voice_mcp/vendor/py-xiaozhi && uv sync`；或设 PY_XIAOZHI_ROOT。"
    )

for p in (str(_XIAOZHI_ROOT), str(_E2E_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from headless_container import HeadlessContainer  # noqa: E402
from probe import EventProbe  # noqa: E402
from src.utils.config_manager import ConfigManager  # noqa: E402

_WS_PATH = ("SYSTEM_OPTIONS", "NETWORK", "WEBSOCKET_URL")


def _override_ws_url_in_memory(url):
    """仅内存覆盖 WEBSOCKET_URL，不落盘（保护 py-xiaozhi 的 config.json）。"""
    cfg = ConfigManager.get_instance()
    node = cfg._config
    for k in _WS_PATH[:-1]:
        node = node.setdefault(k, {})
    old = node.get(_WS_PATH[-1])
    node[_WS_PATH[-1]] = url
    return old


@contextlib.asynccontextmanager
async def run_agent(connect=True, ws_url=None, bringup_timeout=5.0, connect_timeout=12.0):
    """启动一个 headless 小智客户端（脚手架），yield (container, probe)。

    ws_url 不传 = 用 py-xiaozhi config.json 里的地址（官方 tenclass，须已激活）。
    """
    _restore_ws = None
    if ws_url is not None:
        _restore_ws = _override_ws_url_in_memory(ws_url)

    container = HeadlessContainer()
    run_task = asyncio.create_task(
        container.run(protocol="websocket", mode="headless")
    )

    deadline = asyncio.get_event_loop().time() + bringup_timeout
    while asyncio.get_event_loop().time() < deadline:
        if run_task.done():
            await run_task
        if container._plugin_commands is not None:
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.8)

    probe = EventProbe(container.event_bus)
    probe.attach()

    if connect:
        ok = await container.protocol.connect(timeout=connect_timeout)
        if not ok:
            container.tasks.request_shutdown()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(run_task, timeout=5)
            raise RuntimeError("WebSocket 连接失败（检查 WEBSOCKET_URL / 服务器在线 / 设备已激活）")

    try:
        yield container, probe
    finally:
        probe.detach()
        with contextlib.suppress(Exception):
            container.tasks.request_shutdown()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(run_task, timeout=6)
        if not run_task.done():
            run_task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await run_task
        if ws_url is not None:
            _override_ws_url_in_memory(_restore_ws)
