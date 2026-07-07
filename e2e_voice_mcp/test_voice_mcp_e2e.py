"""全生产链路 E2E：语音 → 官方 ASR → 官方 LLM → warehouse MCP(经 wss 接入点) → 后端。

这是最高保真的回归：没有任何环节被模拟。
  合成语音(WAV) → py-xiaozhi headless 上行 → 官方 tenclass ASR
  → 官方云端 LLM(function_call) → 经官方 wss MCP 接入点调 warehouse 工具
  → warehouse mcp_pipe.py → warehouse_mcp.py → 后端 API

覆盖了 mcp_pipe.py 的 WS 外拨生产链路（本地 server 方案测不到的盲区）。

前置条件（不满足则 skip）：
  1. warehouse 后端在 :2124（`PORT=2124 uv run python run_backend.py`）
  2. mcp_pipe.py 已连官方接入点并挂 warehouse：
     `MCP_ENDPOINT=<wss> WAREHOUSE_API_KEY=... PORT=2124 \
      uv run python mcp/mcp_pipe.py mcp/warehouse_mcp.py`
     该 wss 接入点须绑定到 py-xiaozhi 设备所属的官方 agent。
  3. 设置 WAREHOUSE_BACKEND_LOG 指向后端日志（默认取 scratchpad 路径）。

工具调用发生在云端，设备侧看不到 tools/call；因此“工具确实执行”的硬证据
= warehouse 后端访问日志在本轮新增了 MCP 驱动的 API 命中（fuzzy-match /
product-stats / materials 等），而非依赖 LLM 自然语言（可能幻觉）。
"""

import os
import pathlib
import socket

import pytest

from conftest import run_agent
from voice_inject import inject_wav

_WAV = pathlib.Path(__file__).resolve().parent / "fixtures/wav/query_stock_luosi.wav"
_BACKEND_LOG = os.environ.get(
    "WAREHOUSE_BACKEND_LOG",
    str(pathlib.Path(__file__).resolve().parent / "logs/backend.log"),
)
# MCP 工具打后端时会命中的 API（warehouse_mcp.py 的工具实现）
_TOOL_API_MARKERS = ("fuzzy-match", "product-stats", "/api/materials", "/api/batch", "query")


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _read_log_lines():
    p = pathlib.Path(_BACKEND_LOG)
    if not p.exists():
        return None
    return p.read_text(errors="ignore").splitlines()


requires_stack = pytest.mark.skipif(
    not _port_open(2124) or _read_log_lines() is None,
    reason="需 warehouse 后端(:2124) + 可读的后端日志；见模块 docstring 前置条件",
)


@requires_stack
@pytest.mark.asyncio
async def test_voice_to_warehouse_mcp_tool_executed():
    before = len(_read_log_lines())

    async with run_agent() as (container, probe):
        await inject_wav(container, _WAV)

        stt = await probe.wait_json(type="stt", timeout=20)
        print("\n=== ASR ===", stt.get("text"))
        assert "库存" in stt.get("text", "") or "螺丝" in stt.get("text", "")

        # 等 LLM 走完 function-call 往返并应答
        await probe.wait_json(type="tts", state="sentence_start", timeout=30)
        import asyncio

        await asyncio.sleep(8)  # 给云端→wss→后端的工具往返留时间

    # 硬证据：后端日志本轮新增了 MCP 工具驱动的 API 命中
    after = _read_log_lines()
    new_lines = "\n".join(after[before:])
    print("=== 本轮后端新增访问 ===\n", new_lines[-800:])
    print("=== LLM 应答 ===", probe.texts("tts"))

    assert any(m in new_lines for m in _TOOL_API_MARKERS), (
        "warehouse 后端未收到 MCP 工具驱动的 API 命中——"
        "说明官方 LLM 没经 wss 接入点调用 warehouse 工具。"
        f"\n新增日志：\n{new_lines[-1200:]}"
    )
