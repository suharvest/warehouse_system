"""人脸拒绝时的播报文案：必须可执行，且不得泄漏内部枚举名。

回归一个现场事故：被动活体判定为翻拍时后端回 failure_reason="spoof"，MCP 的
兜底分支把它原样拼进播报 ——「人脸校验未通过：spoof」。一个裸英文词扔给中文
语音，LLM 只能自己翻译，现场听到的是「实体验证失败」，一个系统里根本不存在、
也无法检索的说法，排查时在代码里 grep 不到任何东西。
"""

import importlib
import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(REPO_ROOT, 'mcp')
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)


def _mcp():
    return importlib.import_module("warehouse_mcp")


# 后端 orchestrator / endpoint_client / _face_guard 能产出的 reason 全集。
ALL_REASONS = [
    "spoof", "no_face_detected", "device_no_identity", "speaker_unresolved",
    "device_unresolved", "endpoint_unreachable", "endpoint_not_configured",
    "no_match", "low_confidence", "not_in_allow_list",
    # 兜底路径：未预料到的 reason 也必须给出人话
    "infer_bad_response", "infer_no_embedding", "http_502",
    "transport_error", "denied",
]


@pytest.mark.parametrize("reason", ALL_REASONS)
def test_message_never_leaks_the_reason_code(reason):
    """回归点：播报里出现 'spoof' 这类枚举名，就是这个 bug 本身。"""
    msg = _mcp()._face_deny_message(reason)
    assert reason not in msg, f"内部枚举 {reason!r} 泄漏进了播报文案"
    # 顺带挡住任何裸 ASCII 单词（中文播报里不该有）
    assert not re.search(r"[A-Za-z]{4,}", msg), f"播报含英文单词: {msg}"


@pytest.mark.parametrize("reason", ALL_REASONS)
def test_message_is_actionable(reason):
    """每条都要告诉现场的人「现在该做什么」。"""
    msg = _mcp()._face_deny_message(reason)
    assert len(msg) >= 15
    assert "请" in msg, f"没有给出下一步动作: {msg}"


def test_spoof_explains_liveness_specifically():
    """活体失败是最容易被误判的一类，必须点明翻拍/光线，而不是泛泛说校验失败。"""
    msg = _mcp()._face_deny_message("spoof")
    assert "活体" in msg
    assert "照片" in msg or "翻拍" in msg
    assert "光线" in msg or "逆光" in msg


def test_unknown_reason_falls_back_without_leaking():
    msg = _mcp()._face_deny_message("something_totally_new")
    assert "something_totally_new" not in msg
    assert "请" in msg


def test_blocked_stock_in_still_marked_as_not_executed():
    """文案变了，反幻觉契约不能跟着变：仍须是 fail + 未执行前缀 + notice。"""
    m = _mcp()
    blocked = {
        "success": False,
        "error": "face_auth_denied:spoof",
        "message": m._face_deny_message("spoof"),
    }
    out = m._wrap_response("stock_in", blocked)
    assert out["ok"] is False
    assert out["executed"] is False
    assert out["say"].startswith("【操作失败，未执行】本次没有入库。")
    assert "库存没有任何变化" in out["notice"]
    assert "spoof" not in out["say"]
