"""语音注入回归（官方服务器）：合成语音 → 官方 ASR → LLM 应答。

证明无人值守语音注入链路成立（无麦克风/无声卡）：
  TTS 合成 WAV → Opus 上行 → 官方真实 ASR → stt 回显 → LLM tts 文本应答

这是绕开"官方拒文本注入"的正解：走真实音频过 ASR。
warehouse 工具调用需 Phase 2（把 warehouse MCP 经 wss 接入点绑到该 agent），
本用例只验证语音链路本身，断言 ASR 识别 + 有 LLM 应答。
"""

import pathlib

import pytest

from conftest import run_agent
from voice_inject import inject_wav

_WAV = pathlib.Path(__file__).resolve().parent / "fixtures/wav/query_stock_luosi.wav"


@pytest.mark.asyncio
async def test_voice_injection_asr_and_response():
    async with run_agent() as (container, probe):
        await inject_wav(container, _WAV)

        # 官方 ASR 识别结果
        stt = await probe.wait_json(type="stt", timeout=20)
        text = stt.get("text", "")
        print("\n=== ASR 识别 ===", text)

        # 关键词命中即认可识别正确（ASR 可能带标点/细微差异）
        assert "库存" in text or "螺丝" in text, f"ASR 识别异常: {text!r}"

        # 应有 LLM 应答（tts sentence_start 带文本）
        await probe.wait_json(type="tts", state="sentence_start", timeout=25)
        replies = probe.texts("tts")
        print("=== LLM 应答 ===", replies)
        assert replies, f"未收到 LLM 应答；事件={probe.types()}"
