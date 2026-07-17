"""Smoke tests for backend.face Phase 1.

Covers the orchestrator decision ladder with the endpoint client
mocked out so we never actually hit a network. We use a plain
sqlite3 connection (no FastAPI client) and call the async public
API directly.

Identity model (post-refactor): enrollments are bound to
`face_subjects` (people), not to system `users`. A subject is the
unit of authorization; the calling system user is just identified
for audit. There is no "user_mismatch" check anymore.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import numpy as np
import pytest


# This module exercises the face orchestrator against a literal sqlite
# tempfile (not the SA engine). Skip wholesale when the test session is
# pinned to a non-sqlite DATABASE_URL — the orchestrator + fixture both
# assume sqlite3 semantics (`?` placeholders, INSERT OR IGNORE, ...).
_db_url = os.environ.get('DATABASE_URL', '')
if _db_url and not _db_url.startswith('sqlite'):
    pytest.skip(
        "test_face.py is sqlite-only (orchestrator uses raw sqlite3 + sqlite-specific SQL)",
        allow_module_level=True,
    )


# Make the backend package importable as `backend.face`
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _emb_bytes(vec):
    """Pack a python list to a float32 LE byte buffer (the storage format)."""
    return np.asarray(vec, dtype=np.float32).tobytes()


@pytest.fixture()
def conn(monkeypatch):
    """Fresh SQLite db with the face tables (and minimal tenants/users) set up."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_PATH", path)

    # Reload database module so it picks up DATABASE_PATH
    import importlib
    if "database" in sys.modules:
        del sys.modules["database"]
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    import database  # noqa: E402
    importlib.reload(database)
    database.init_database()

    c = database.get_db_connection()
    cur = c.cursor()
    # Make sure we have a tenant=1 and a couple of users (caller identities).
    cur.execute("INSERT OR IGNORE INTO tenants (id, slug, name) VALUES (1, 'default', 'Default')")
    cur.execute(
        "INSERT OR IGNORE INTO users (id, username, password_hash, role, tenant_id) "
        "VALUES (101, 'alice', 'x', 'operate', 1)"
    )
    cur.execute(
        "INSERT OR IGNORE INTO users (id, username, password_hash, role, tenant_id) "
        "VALUES (102, 'bob', 'x', 'operate', 1)"
    )
    c.commit()
    yield c
    c.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def _set_config(conn, *, enabled: bool, min_confidence: float = 0.65, verify_mode: str = "interface"):
    cur = conn.cursor()
    cur.execute("DELETE FROM tenant_face_config WHERE tenant_id = 1")
    cur.execute(
        """
        INSERT INTO tenant_face_config
            (tenant_id, enabled, mode, endpoint, embedding_model_tag, min_confidence, verify_mode)
        VALUES (1, ?, 'lan', 'http://fake.local', 'fake-v1', ?, ?)
        """,
        (1 if enabled else 0, min_confidence, verify_mode),
    )
    conn.commit()


def _set_rule(conn, *, require_face: bool, allowed_subject_ids=None, operation="stock_out", warehouse_id=None):
    cur = conn.cursor()
    cur.execute("DELETE FROM tenant_face_operation_rules WHERE tenant_id = 1")
    cur.execute(
        """
        INSERT INTO tenant_face_operation_rules
            (tenant_id, warehouse_id, operation, require_face, allowed_subject_ids)
        VALUES (1, ?, ?, ?, ?)
        """,
        (
            warehouse_id,
            operation,
            1 if require_face else 0,
            json.dumps(allowed_subject_ids) if allowed_subject_ids else None,
        ),
    )
    conn.commit()


def _create_subject(conn, name: str, employee_id: str | None = None) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO face_subjects (tenant_id, name, employee_id, is_active)
        VALUES (1, ?, ?, 1)
        """,
        (name, employee_id),
    )
    conn.commit()
    return int(cur.lastrowid)


def _enroll(conn, subject_id: int, vec, model_tag: str = "fake-v1"):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO face_enrollments
            (subject_id, tenant_id, model_tag, embedding, is_active)
        VALUES (?, 1, ?, ?, 1)
        """,
        (subject_id, model_tag, _emb_bytes(vec)),
    )
    conn.commit()


# ── tests ──

def test_enroll_face_persists_n(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    async def fake_infer(cfg, image_b64):
        return {"embedding": _emb_bytes([1.0, 0.0, 0.0]), "model_tag": "fake-v1"}

    monkeypatch.setattr(endpoint_client, "infer", fake_infer)
    _set_config(conn, enabled=True)
    sid = _create_subject(conn, "Alice Person")

    out = asyncio.run(orchestrator.enroll_face(
        conn,
        subject_id=sid,
        tenant_id=1,
        images_b64=["img1", "img2", "img3"],
    ))
    assert out["count"] == 3
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM face_enrollments WHERE subject_id = ?", (sid,))
    assert cur.fetchone()["n"] == 3


def test_verify_skipped_when_disabled(conn):
    from backend.face import verify_mcp_face
    _set_config(conn, enabled=False)
    _set_rule(conn, require_face=True)
    decision = asyncio.run(verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
        image_b64="",
    ))
    assert decision.status == "skipped"
    assert decision.failure_reason == "feature_disabled"


def test_verify_skipped_when_rule_not_required(conn):
    from backend.face import verify_mcp_face
    _set_config(conn, enabled=True)
    _set_rule(conn, require_face=False)
    decision = asyncio.run(verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
        image_b64="",
    ))
    assert decision.status == "skipped"


def test_verify_deny_when_image_missing(conn):
    """Feature on + rule requires face + caller forgot to attach an image."""
    from backend.face import verify_mcp_face
    _set_config(conn, enabled=True)
    _set_rule(conn, require_face=True)
    decision = asyncio.run(verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
        image_b64="",
    ))
    assert decision.status == "deny"
    assert decision.failure_reason == "no_image_provided"


def test_verify_pass_when_subject_matches_and_allowed(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    target = [1.0, 0.0, 0.0]

    async def fake_infer(cfg, image_b64):
        return {"embedding": _emb_bytes(target), "model_tag": "fake-v1"}

    monkeypatch.setattr(endpoint_client, "infer", fake_infer)

    _set_config(conn, enabled=True, min_confidence=0.5)
    sid = _create_subject(conn, "Person A")
    _enroll(conn, sid, target)
    _set_rule(conn, require_face=True, allowed_subject_ids=[sid])

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
        image_b64="snap",
    ))
    assert decision.status == "pass", decision
    assert decision.matched_subject_id == sid
    assert decision.confidence is not None and decision.confidence > 0.99


def test_verify_deny_low_confidence(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    async def fake_infer(cfg, image_b64):
        # nearly orthogonal to enrolled vector -> very low cosine
        return {"embedding": _emb_bytes([0.0, 1.0, 0.0]), "model_tag": "fake-v1"}

    monkeypatch.setattr(endpoint_client, "infer", fake_infer)

    _set_config(conn, enabled=True, min_confidence=0.65)
    sid = _create_subject(conn, "Person A")
    _enroll(conn, sid, [1.0, 0.0, 0.0])
    _set_rule(conn, require_face=True)

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
        image_b64="snap",
    ))
    assert decision.status == "deny"
    assert decision.failure_reason == "low_confidence"


def test_verify_deny_subject_not_in_allow_list(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    async def fake_infer(cfg, image_b64):
        return {"embedding": _emb_bytes([0.0, 1.0, 0.0]), "model_tag": "fake-v1"}

    monkeypatch.setattr(endpoint_client, "infer", fake_infer)

    _set_config(conn, enabled=True, min_confidence=0.5)
    sid_b = _create_subject(conn, "Person B")
    sid_allowed = _create_subject(conn, "Person Allowed")
    # The enrolled vector matches subject B, but rule only allows the other one.
    _enroll(conn, sid_b, [0.0, 1.0, 0.0])
    _set_rule(conn, require_face=True, allowed_subject_ids=[sid_allowed])

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
        image_b64="snap",
    ))
    assert decision.status == "deny"
    assert decision.failure_reason == "not_in_allow_list"
    assert decision.matched_subject_id == sid_b


def test_enroll_face_with_precomputed_embeddings(conn):
    """Device-side path (WE2): caller provides embeddings directly, no /infer."""
    from backend.face import orchestrator
    _set_config(conn, enabled=True)
    sid = _create_subject(conn, "Person WE2")

    out = asyncio.run(orchestrator.enroll_face(
        conn,
        subject_id=sid,
        tenant_id=1,
        precomputed=[
            {"embedding_bytes": _emb_bytes([1.0, 0.0, 0.0]), "model_tag": "we2-mfn128-v1"},
            {"embedding_bytes": _emb_bytes([0.99, 0.01, 0.0]), "model_tag": "we2-mfn128-v1"},
        ],
    ))
    assert out["count"] == 2
    cur = conn.cursor()
    cur.execute(
        "SELECT model_tag, source_image_b64 FROM face_enrollments WHERE subject_id = ?",
        (sid,),
    )
    rows = cur.fetchall()
    assert len(rows) == 2
    assert all(r["model_tag"] == "we2-mfn128-v1" for r in rows)
    assert all(r["source_image_b64"] is None for r in rows)


def test_verify_pass_with_precomputed_embedding(conn):
    """Device-side path (WE2): match against pre-stored embedding without calling /infer."""
    from backend.face import orchestrator
    target = [1.0, 0.0, 0.0]
    _set_config(conn, enabled=True, min_confidence=0.5)
    sid = _create_subject(conn, "Person WE2")
    _enroll(conn, sid, target, model_tag="we2-mfn128-v1")
    _set_rule(conn, require_face=True, allowed_subject_ids=[sid])

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
        embedding_bytes=_emb_bytes(target),
        embedding_model_tag="we2-mfn128-v1",
    ))
    assert decision.status == "pass", decision
    assert decision.matched_subject_id == sid


def test_verify_deny_endpoint_unreachable(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    async def fake_infer(cfg, image_b64):
        raise endpoint_client.FaceEndpointError("endpoint_unreachable")

    monkeypatch.setattr(endpoint_client, "infer", fake_infer)

    _set_config(conn, enabled=True)
    _set_rule(conn, require_face=True)

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
        image_b64="snap",
    ))
    assert decision.status == "deny"
    assert decision.failure_reason == "endpoint_unreachable"


# ── Session mode: backend-direct device pull (B 方案) ───────────────────────
# session 模式不信任 LLM 转发的 speaker_* 参数（可被提示注入伪造），改由后端直连
# 设备拉取身份。测试通过 mock ``device_pull.pull_current_speaker`` 模拟设备应答。
# 一切"不是一个当场、可解析、被允许的身份"→ deny（fail-closed）。

class _FakeDevice:  # stand-in for a resolved PullDevice (needs ip/port for cache key)
    ip = "10.0.0.9"
    port = 80


_SENTINEL_DEVICE = _FakeDevice()


def _verify_session(conn, *, device_identity="__none__", **kw):
    """Run session-mode verify with a mocked device pull.

    device_identity: dict returned by the device (valid/subject_id/name/...);
    None → device returned nothing (HTTP error/timeout/busy); "__none__" → no
    pull_device resolvable at all (device_unresolved). Clears the 仅首次 cache
    each call so cases are independent.
    """
    import backend.face.device_pull as device_pull
    from backend.face import orchestrator

    orchestrator._verify_once_cache.clear()

    async def _fake_pull(dev, *, fresh=1):
        return None if device_identity == "__none__" else device_identity

    pull_device = None if device_identity == "__none__" else _SENTINEL_DEVICE
    orig = device_pull.pull_current_speaker
    device_pull.pull_current_speaker = _fake_pull
    try:
        return asyncio.run(orchestrator.verify_mcp_face(
            conn, tenant_id=1, user_id=101, warehouse_id=None,
            operation="stock_out", pull_device=pull_device, **kw
        ))
    finally:
        device_pull.pull_current_speaker = orig


def test_session_no_device_resolvable_denies(conn):
    """无法为该 API Key 定位设备 → device_unresolved deny。"""
    _set_config(conn, enabled=True, verify_mode="session")
    _set_rule(conn, require_face=True)

    decision = _verify_session(conn, device_identity="__none__")
    assert decision.status == "deny"
    assert decision.failure_reason == "device_unresolved"


def test_session_device_no_identity_denies(conn):
    """设备应答无效身份（没拍到人/陌生人/忙/超时）→ device_no_identity deny。"""
    _set_config(conn, enabled=True, verify_mode="session")
    _set_rule(conn, require_face=True)

    decision = _verify_session(conn, device_identity={"valid": False})
    assert decision.status == "deny"
    assert decision.failure_reason == "device_no_identity"


def test_session_ignores_llm_forwarded_identity(conn):
    """即便 LLM 传了合法 speaker_subject_id，session 也只认设备拉取结果。"""
    _set_config(conn, enabled=True, verify_mode="session")
    sid = _create_subject(conn, "Real Person")
    _set_rule(conn, require_face=True)

    # LLM 伪造了 sid，但设备说没人 → 必须 deny（不被 LLM 参数骗过）。
    decision = _verify_session(
        conn, device_identity={"valid": False}, speaker_subject_id=sid,
    )
    assert decision.status == "deny"
    assert decision.failure_reason == "device_no_identity"


def test_session_device_subject_passes(conn):
    """设备上报有效 subject_id → 解析放行，confidence 取设备 similarity。"""
    _set_config(conn, enabled=True, verify_mode="session")
    sid = _create_subject(conn, "Session Speaker")
    _set_rule(conn, require_face=True)

    decision = _verify_session(
        conn, device_identity={"valid": True, "subject_id": sid, "similarity": 0.83},
    )
    assert decision.status == "pass"
    assert decision.failure_reason == "session_verified"
    assert decision.matched_subject_id == sid
    assert decision.confidence == 0.83


def test_session_device_name_only_passes(conn):
    """lan 模式设备只给 name(subject_id=0) → 按姓名解析放行。"""
    _set_config(conn, enabled=True, verify_mode="session")
    sid = _create_subject(conn, "By Name")
    _set_rule(conn, require_face=True)

    decision = _verify_session(
        conn, device_identity={"valid": True, "subject_id": 0, "name": "By Name"},
    )
    assert decision.status == "pass"
    assert decision.matched_subject_id == sid


def test_session_device_name_ambiguous_denies(conn):
    """lan 设备只回 name，但同租户有两个同名 active subject → 无法确定 → deny。"""
    _set_config(conn, enabled=True, verify_mode="session")
    _create_subject(conn, "Dupe Name")
    _create_subject(conn, "Dupe Name")
    _set_rule(conn, require_face=True)

    decision = _verify_session(
        conn, device_identity={"valid": True, "subject_id": 0, "name": "Dupe Name"},
    )
    assert decision.status == "deny"
    assert decision.failure_reason == "speaker_unresolved"


def test_session_device_subject_not_in_allow_list_denies(conn):
    _set_config(conn, enabled=True, verify_mode="session")
    sid_allowed = _create_subject(conn, "Allowed")
    sid_other = _create_subject(conn, "Other")
    _set_rule(conn, require_face=True, allowed_subject_ids=[sid_allowed])

    decision = _verify_session(
        conn, device_identity={"valid": True, "subject_id": sid_other, "similarity": 0.9},
    )
    assert decision.status == "deny"
    assert decision.failure_reason == "not_in_allow_list"
    assert decision.matched_subject_id == sid_other


def test_session_verify_once_per_conversation(conn):
    """仅首次：同一 conv_seq 首笔 fresh=1 验证并缓存，之后同 conv_seq 免验(session_cached，
    不再 fresh=1)；新 conv_seq 重新 fresh=1 验证。"""
    import backend.face.device_pull as device_pull
    from backend.face import orchestrator

    orchestrator._verify_once_cache.clear()
    sid = _create_subject(conn, "Conv Person")
    _set_config(conn, enabled=True, verify_mode="session")
    _set_rule(conn, require_face=True)

    calls = {"fresh1": 0, "fresh0": 0}
    state = {"conv_seq": 5}

    async def _fake_pull(dev, *, fresh=1):
        if fresh == 1:
            calls["fresh1"] += 1
            return {"valid": True, "subject_id": sid, "similarity": 0.7,
                    "conv_seq": state["conv_seq"]}
        calls["fresh0"] += 1  # cheap conv_seq read
        return {"valid": True, "subject_id": sid, "conv_seq": state["conv_seq"]}

    orig = device_pull.pull_current_speaker
    device_pull.pull_current_speaker = _fake_pull

    def _run():
        return asyncio.run(orchestrator.verify_mcp_face(
            conn, tenant_id=1, user_id=101, warehouse_id=None,
            operation="stock_out", pull_device=_SENTINEL_DEVICE,
        ))
    try:
        d1 = _run()  # 首笔：fresh=1 验证 + 缓存
        assert d1.status == "pass" and d1.failure_reason == "session_verified"
        assert calls == {"fresh1": 1, "fresh0": 0}

        d2 = _run()  # 同 conv_seq：免验（只 fresh=0 读 seq）
        assert d2.status == "pass" and d2.failure_reason == "session_cached"
        assert d2.matched_subject_id == sid
        assert calls == {"fresh1": 1, "fresh0": 1}  # 没有新增 fresh=1

        state["conv_seq"] = 6  # 新对话
        d3 = _run()  # conv_seq 变了：重新 fresh=1 验证
        assert d3.status == "pass" and d3.failure_reason == "session_verified"
        assert calls["fresh1"] == 2
    finally:
        device_pull.pull_current_speaker = orig
        orchestrator._verify_once_cache.clear()


def test_session_cached_denies_deactivated_subject(conn):
    """缓存命中但该 subject 已停用 → 作废缓存、重验(此处设备也无有效身份)→ deny。"""
    import backend.face.device_pull as device_pull
    from backend.face import orchestrator

    orchestrator._verify_once_cache.clear()
    sid = _create_subject(conn, "Will Deactivate")
    _set_config(conn, enabled=True, verify_mode="session")
    _set_rule(conn, require_face=True)

    fresh1_returns = [{"valid": True, "subject_id": sid, "similarity": 0.7, "conv_seq": 9}]

    async def _fake_pull(dev, *, fresh=1):
        if fresh == 1:
            return fresh1_returns[0]
        return {"valid": True, "subject_id": sid, "conv_seq": 9}  # same conv

    orig = device_pull.pull_current_speaker
    device_pull.pull_current_speaker = _fake_pull

    def _run():
        return asyncio.run(orchestrator.verify_mcp_face(
            conn, tenant_id=1, user_id=101, warehouse_id=None,
            operation="stock_out", pull_device=_SENTINEL_DEVICE,
        ))
    try:
        assert _run().status == "pass"  # 首笔缓存
        # 停用该人；同对话再来 → 缓存作废、重验；设备现在也给不出可解析身份
        cur = conn.cursor()
        cur.execute("UPDATE face_subjects SET is_active = 0 WHERE id = ?", (sid,))
        conn.commit()
        fresh1_returns[0] = {"valid": True, "subject_id": sid, "similarity": 0.7, "conv_seq": 9}
        d = _run()
        assert d.status == "deny"  # 停用 → speaker_unresolved
        assert d.failure_reason == "speaker_unresolved"
    finally:
        device_pull.pull_current_speaker = orig
        orchestrator._verify_once_cache.clear()


def test_session_device_inactive_subject_denies(conn):
    """设备上报的 subject 已停用 → 解析失败(不回退姓名) → speaker_unresolved deny。"""
    _set_config(conn, enabled=True, verify_mode="session")
    sid = _create_subject(conn, "Inactive")
    cur = conn.cursor()
    cur.execute("UPDATE face_subjects SET is_active = 0 WHERE id = ?", (sid,))
    conn.commit()
    _set_rule(conn, require_face=True)

    decision = _verify_session(
        conn, device_identity={"valid": True, "subject_id": sid, "similarity": 0.9},
    )
    assert decision.status == "deny"
    assert decision.failure_reason == "speaker_unresolved"


# ── endpoint_client /infer parsing: passive liveness (spoof) ─────────────


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stub for httpx.AsyncClient returning a canned /infer payload."""

    payload = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return _FakeResponse(type(self).payload)


def _lan_cfg():
    from backend.face.models import FaceConfig
    return FaceConfig(
        tenant_id=1, enabled=True, mode="lan",
        endpoint="http://fake:8001", auth_token=None,
        embedding_model_tag=None, min_confidence=0.65,
    )


def _run_infer(monkeypatch, payload):
    from backend.face import endpoint_client
    _FakeAsyncClient.payload = payload
    monkeypatch.setattr(endpoint_client.httpx, "AsyncClient", _FakeAsyncClient)
    return asyncio.run(endpoint_client.infer(_lan_cfg(), "aW1n"))


def test_infer_spoof_face_raises_spoof(monkeypatch):
    """face_rec_api reject 模式：假体脸 live=false + embedding=null → spoof 错误码。"""
    from backend.face.endpoint_client import FaceEndpointError
    with pytest.raises(FaceEndpointError, match="^spoof$"):
        _run_infer(monkeypatch, {
            "model_tag": "hailo:x", "face_count": 1,
            "faces": [{"det_score": 0.9, "embedding": None,
                       "live": False, "liveness_score": 0.01}],
        })


def test_infer_live_face_with_liveness_fields_ok(monkeypatch):
    """live=true 带活体字段照常返回 embedding（向后兼容）。"""
    import base64 as b64
    emb = _emb_bytes([0.1] * 4)
    result = _run_infer(monkeypatch, {
        "model_tag": "hailo:x", "face_count": 1,
        "faces": [{"det_score": 0.9, "embedding": b64.b64encode(emb).decode(),
                   "live": True, "liveness_score": 0.99}],
    })
    assert result["embedding"] == emb


def test_infer_no_liveness_fields_ok(monkeypatch):
    """活体关闭 / 旧版 face_rec_api：无 live 字段完全不受影响。"""
    import base64 as b64
    emb = _emb_bytes([0.2] * 4)
    result = _run_infer(monkeypatch, {
        "model_tag": "hailo:x", "face_count": 1,
        "faces": [{"det_score": 0.9, "embedding": b64.b64encode(emb).decode()}],
    })
    assert result["embedding"] == emb
