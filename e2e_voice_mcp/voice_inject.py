"""语音注入 —— 把 WAV 当麦克风喂进上行链路（候选 c：绕过 codec 生命周期）。

流程：WAV(16k/mono/int16) → float32 → 切 320 样本/帧 → OpusCodec 编码
→ send_start_listening → 按 20ms 节奏 send_audio 逐帧 → 尾部补静音让服务器
VAD 断句 → (MANUAL 时) send_stop_listening。

服务器对音频做真实 ASR，识别文本经 INCOMING_JSON 以 {type:stt,text} 回来，
用 EventProbe.wait_json(type="stt") 断言。不碰 sounddevice、不需声卡。

依赖 opus：import src.audio_codecs.opus_codec 时其模块顶层已 setup_opus()。
"""

import asyncio
import wave

import numpy as np

from src.audio_codecs.opus_codec import OpusCodec  # 导入即完成 setup_opus()
from src.constants.constants import AudioConfig, ListeningMode

_FRAME = AudioConfig.INPUT_FRAME_SIZE  # 320 @ 16k/20ms
_SR = AudioConfig.INPUT_SAMPLE_RATE    # 16000


def wav_to_opus_frames(wav_path, encoder=None):
    """WAV(16k/mono/int16) → list[bytes] Opus 帧（每帧 20ms/320 样本）。"""
    with wave.open(str(wav_path), "rb") as w:
        assert w.getnchannels() == 1, "需单声道"
        assert w.getframerate() == _SR, f"需 {_SR}Hz，实际 {w.getframerate()}"
        assert w.getsampwidth() == 2, "需 int16"
        raw = w.readframes(w.getnframes())

    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    enc = encoder or _new_encoder()
    frames = []
    for i in range(0, len(pcm), _FRAME):
        chunk = pcm[i : i + _FRAME]
        if len(chunk) < _FRAME:
            chunk = np.pad(chunk, (0, _FRAME - len(chunk)))
        frames.append(enc.encode(np.ascontiguousarray(chunk), _FRAME))
    return frames


def silence_frames(ms, encoder=None):
    """生成 ms 毫秒的静音 Opus 帧，供 AUTO_STOP 触发服务器端 VAD 断句。"""
    enc = encoder or _new_encoder()
    n = max(1, int(ms / AudioConfig.FRAME_DURATION))
    zero = np.zeros(_FRAME, dtype=np.float32)
    return [enc.encode(zero, _FRAME) for _ in range(n)]


def _new_encoder():
    c = OpusCodec(_SR, AudioConfig.OUTPUT_SAMPLE_RATE)
    c.initialize()
    return c


async def inject_wav(
    container,
    wav_path,
    mode=ListeningMode.MANUAL,
    frame_interval=0.02,
    tail_silence_ms=300,
):
    """把 wav_path 作为一轮用户语音注入。

    默认 MANUAL：发完语音显式 send_stop_listening() 触发服务器 ASR 断句
    —— 官方 tenclass 实测唯一可靠方式（AUTO_STOP 的服务器端 VAD 对合成
    静音帧不稳定触发）。MANUAL 也让测试确定性更好。
    """
    enc = _new_encoder()
    speech = wav_to_opus_frames(wav_path, encoder=enc)
    tail = silence_frames(tail_silence_ms, encoder=enc) if tail_silence_ms else []

    await container.protocol.send_start_listening(mode)
    for f in speech + tail:
        await container.protocol.send_audio(f)
        await asyncio.sleep(frame_interval)

    if mode == ListeningMode.MANUAL:
        await container.protocol.send_stop_listening()
