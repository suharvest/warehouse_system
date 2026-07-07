"""冒烟（官方服务器）：证明 headless 驱动的机械层成立。

重要边界发现：官方 tenclass 服务器对 {type:listen,state:detect,text:...}
只接受**唤醒词短语**，长文本会被拒：
    {"type":"alert","status":"ERROR",
     "message":"Detect is only for wake words, do not send long texts."}
因此**文本注入的对话回归必须打本地 xiaozhi-server**（它把 detect+text
直接路由进 startToChat，不限长度）。见 test_smoke_local.py。

本用例只验证 headless 驱动本身可用（无 GUI / 无 CLI / 无声卡）：
  1. in-process 起容器、WS 连上官方
  2. 收到服务器主动发起的设备 MCP 握手（initialize / tools/list）
     —— 证明 EventBus 观测通道 + McpPlugin 应答链路通
"""

import pytest

from conftest import run_agent


@pytest.mark.asyncio
async def test_headless_connects_and_mcp_handshake():
    async with run_agent() as (container, probe):
        # 服务器连上后主动下发设备 MCP initialize（证明 EventBus 观测通道通）
        await probe.wait_mcp_method("initialize", timeout=15)

        # 设备应答后，服务器继续握手 tools/list 分页拉取设备工具
        # （证明 McpPlugin 应答链路通——服务器只有收到 initialize 结果才会继续）
        await probe.wait_mcp_method("tools/list", timeout=15)

        methods = [
            e["payload"].get("method")
            for e in probe.tool_calls()
            if isinstance(e.get("payload"), dict)
        ]
        print("\n=== 收到的设备 MCP 方法 ===", methods)
