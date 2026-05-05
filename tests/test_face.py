"""Smoke tests for backend.face Phase 1.

Covers the orchestrator decision ladder with the endpoint client
mocked out so we never actually hit a network. We use a plain
sqlite3 connection (no FastAPI client) and call the async public
API directly.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import numpy as np
import pytest


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
    # Make sure we have a tenant=1 and a couple of users
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


def _set_config(conn, *, enabled: bool, min_confidence: float = 0.65):
    cur = conn.cursor()
    cur.execute("DELETE FROM tenant_face_config WHERE tenant_id = 1")
    cur.execute(
        """
        INSERT INTO tenant_face_config
            (tenant_id, enabled, mode, endpoint, embedding_model_tag, min_confidence)
        VALUES (1, ?, 'custom', 'http://fake.local', 'fake-v1', ?)
        """,
        (1 if enabled else 0, min_confidence),
    )
    conn.commit()


def _set_rule(conn, *, require_face: bool, allowed_user_ids=None, operation="stock_out", warehouse_id=None):
    cur = conn.cursor()
    cur.execute("DELETE FROM tenant_face_operation_rules WHERE tenant_id = 1")
    cur.execute(
        """
        INSERT INTO tenant_face_operation_rules
            (tenant_id, warehouse_id, operation, require_face, allowed_user_ids)
        VALUES (1, ?, ?, ?, ?)
        """,
        (
            warehouse_id,
            operation,
            1 if require_face else 0,
            json.dumps(allowed_user_ids) if allowed_user_ids else None,
        ),
    )
    conn.commit()


def _enroll(conn, user_id: int, vec, model_tag: str = "fake-v1"):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO face_enrollments
            (user_id, tenant_id, model_tag, embedding, is_active)
        VALUES (?, 1, ?, ?, 1)
        """,
        (user_id, model_tag, _emb_bytes(vec)),
    )
    conn.commit()


# ── tests ──

def test_enroll_face_persists_n(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    async def fake_infer(cfg, image_b64):
        return {"embedding": _emb_bytes([1.0, 0.0, 0.0]), "model_tag": "fake-v1"}

    monkeypatch.setattr(endpoint_client, "infer", fake_infer)
    # need a config row so enroll can find an endpoint
    _set_config(conn, enabled=True)

    out = asyncio.run(orchestrator.enroll_face(
        conn,
        user_id=101,
        tenant_id=1,
        images_b64=["img1", "img2", "img3"],
    ))
    assert out["count"] == 3
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM face_enrollments WHERE user_id = 101")
    assert cur.fetchone()["n"] == 3


def test_verify_skipped_when_disabled(conn):
    from backend.face import verify_mcp_face
    _set_config(conn, enabled=False)
    _set_rule(conn, require_face=True)
    decision = asyncio.run(verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
    ))
    assert decision.status == "skipped"
    assert decision.failure_reason == "feature_disabled"


def test_verify_skipped_when_rule_not_required(conn):
    from backend.face import verify_mcp_face
    _set_config(conn, enabled=True)
    _set_rule(conn, require_face=False)
    decision = asyncio.run(verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
    ))
    assert decision.status == "skipped"


def test_verify_pass_when_match_correct_user(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    target = [1.0, 0.0, 0.0]

    async def fake_capture(cfg):
        return {"image_b64": "snap", "ts": "now"}

    async def fake_infer(cfg, image_b64):
        return {"embedding": _emb_bytes(target), "model_tag": "fake-v1"}

    monkeypatch.setattr(endpoint_client, "capture", fake_capture)
    monkeypatch.setattr(endpoint_client, "infer", fake_infer)

    _set_config(conn, enabled=True, min_confidence=0.5)
    _set_rule(conn, require_face=True)
    _enroll(conn, 101, target)

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
    ))
    assert decision.status == "pass", decision
    assert decision.matched_user_id == 101
    assert decision.confidence is not None and decision.confidence > 0.99


def test_verify_deny_low_confidence(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    async def fake_capture(cfg):
        return {"image_b64": "snap", "ts": "now"}

    async def fake_infer(cfg, image_b64):
        # nearly orthogonal to enrolled vector -> very low cosine
        return {"embedding": _emb_bytes([0.0, 1.0, 0.0]), "model_tag": "fake-v1"}

    monkeypatch.setattr(endpoint_client, "capture", fake_capture)
    monkeypatch.setattr(endpoint_client, "infer", fake_infer)

    _set_config(conn, enabled=True, min_confidence=0.65)
    _set_rule(conn, require_face=True)
    _enroll(conn, 101, [1.0, 0.0, 0.0])

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
    ))
    assert decision.status == "deny"
    assert decision.failure_reason == "low_confidence"


def test_verify_deny_user_mismatch(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    async def fake_capture(cfg):
        return {"image_b64": "snap", "ts": "now"}

    async def fake_infer(cfg, image_b64):
        return {"embedding": _emb_bytes([0.0, 1.0, 0.0]), "model_tag": "fake-v1"}

    monkeypatch.setattr(endpoint_client, "capture", fake_capture)
    monkeypatch.setattr(endpoint_client, "infer", fake_infer)

    _set_config(conn, enabled=True, min_confidence=0.5)
    _set_rule(conn, require_face=True)
    # The enrolled vector matches user 102, but the request claims to be 101
    _enroll(conn, 102, [0.0, 1.0, 0.0])

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
    ))
    assert decision.status == "deny"
    assert decision.failure_reason == "user_mismatch"
    assert decision.matched_user_id == 102


def test_verify_deny_endpoint_unreachable(conn, monkeypatch):
    from backend.face import endpoint_client, orchestrator

    async def fake_capture(cfg):
        raise endpoint_client.FaceEndpointError("endpoint_unreachable")

    monkeypatch.setattr(endpoint_client, "capture", fake_capture)

    _set_config(conn, enabled=True)
    _set_rule(conn, require_face=True)

    decision = asyncio.run(orchestrator.verify_mcp_face(
        conn, tenant_id=1, user_id=101, warehouse_id=None, operation="stock_out",
    ))
    assert decision.status == "deny"
    assert decision.failure_reason == "endpoint_unreachable"
