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


# ── Session mode (advisory) allow-list enforcement ──────────────────────────
# session 模式信任设备本地匹配、不重比对，但配了 allowed_subject_ids 的规则是
# 硬性限制：说话人未解析（设备没认出人 / 传了停用或跨租户 ID）也必须 deny，
# 否则"谁都不是"反而能通过专门限制操作人的规则。

def _verify_session(conn, **kw):
    from backend.face import orchestrator
    return asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out", **kw
    ))


def test_session_allow_list_denies_unresolved_speaker(conn):
    _set_config(conn, enabled=True, verify_mode="session")
    sid = _create_subject(conn, "Session Allowed")
    _set_rule(conn, require_face=True, allowed_subject_ids=[sid])

    decision = _verify_session(conn)  # no speaker at all
    assert decision.status == "deny"
    assert decision.failure_reason == "speaker_unresolved"


def test_session_allow_list_denies_other_subject(conn):
    _set_config(conn, enabled=True, verify_mode="session")
    sid_allowed = _create_subject(conn, "Session Allowed")
    sid_other = _create_subject(conn, "Session Other")
    _set_rule(conn, require_face=True, allowed_subject_ids=[sid_allowed])

    decision = _verify_session(conn, speaker_subject_id=sid_other)
    assert decision.status == "deny"
    assert decision.failure_reason == "not_in_allow_list"
    assert decision.matched_subject_id == sid_other


def test_session_allow_list_passes_allowed_subject(conn):
    _set_config(conn, enabled=True, verify_mode="session")
    sid = _create_subject(conn, "Session Allowed")
    _set_rule(conn, require_face=True, allowed_subject_ids=[sid])

    decision = _verify_session(conn, speaker_subject_id=sid)
    assert decision.status == "pass"
    assert decision.matched_subject_id == sid


def test_session_no_allow_list_passes_unresolved(conn):
    """无白名单的规则保持 advisory 语义：未识别也放行，仅记审计。"""
    _set_config(conn, enabled=True, verify_mode="session")
    _set_rule(conn, require_face=True)

    decision = _verify_session(conn)
    assert decision.status == "pass"
    assert decision.matched_subject_id is None


def test_session_inactive_subject_id_not_resolved(conn):
    """停用人员的 speaker_subject_id 不再解析成功（等同未识别）。"""
    _set_config(conn, enabled=True, verify_mode="session")
    sid = _create_subject(conn, "Session Inactive")
    cur = conn.cursor()
    cur.execute("UPDATE face_subjects SET is_active = 0 WHERE id = ?", (sid,))
    conn.commit()
    _set_rule(conn, require_face=True, allowed_subject_ids=[sid])

    decision = _verify_session(conn, speaker_subject_id=sid)
    assert decision.status == "deny"
    assert decision.failure_reason == "speaker_unresolved"
