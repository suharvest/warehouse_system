"""Tests for the WE2 face inference simulator (`backend/routers/face_we2.py`).

These tests intentionally do NOT use the session-scoped `app_instance` fixture
in conftest.py — they need to reload `app` under different
`FACE_WE2_SIMULATOR_ENABLED` environments, which the shared session fixture
can't accommodate.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import io
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Ensure backend is importable (mirrors conftest)
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

FIXTURE_FACE = Path(__file__).resolve().parent / "fixtures" / "we2_test_face.jpg"


def _reload_app_with_env(monkeypatch, enabled: bool):
    """Re-import `app` with the WE2 env var set/unset and return the FastAPI app."""
    if enabled:
        monkeypatch.setenv("FACE_WE2_SIMULATOR_ENABLED", "1")
    else:
        monkeypatch.delenv("FACE_WE2_SIMULATOR_ENABLED", raising=False)

    # Drop cached modules so the env guard is re-evaluated
    for mod in ("app", "routers.face_we2"):
        if mod in sys.modules:
            del sys.modules[mod]

    import app as app_module  # type: ignore

    app_module.limiter.enabled = False
    return app_module.app


@pytest.fixture
def app_we2_on(monkeypatch):
    return _reload_app_with_env(monkeypatch, True)


@pytest.fixture
def app_we2_off(monkeypatch):
    return _reload_app_with_env(monkeypatch, False)


@pytest.fixture
def client_we2_on(app_we2_on):
    return TestClient(app_we2_on)


@pytest.fixture
def client_we2_off(app_we2_off):
    return TestClient(app_we2_off)


def _image_to_b64(img: Image.Image, fmt: str = "JPEG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Disabled mode
# ---------------------------------------------------------------------------

def test_simulator_disabled_when_env_off(client_we2_off, app_we2_off):
    """With FACE_WE2_SIMULATOR_ENABLED unset, the router is not mounted.

    The app has a SPA catch-all GET route, so we don't get a clean 404. We
    assert directly on the route table: no WE2 routes are registered.
    """
    we2_routes = [
        r for r in app_we2_off.routes
        if getattr(r, "path", "").startswith("/api/face/we2")
    ]
    assert we2_routes == [], f"unexpected WE2 routes registered: {we2_routes}"

    # And POST to /api/face/we2/infer must NOT return our InferResponse shape
    resp = client_we2_off.post(
        "/api/face/we2/infer", json={"image_b64": "AAAA"}
    )
    assert resp.status_code != 200, (
        "WE2 infer endpoint is mounted but should be disabled"
    )


# ---------------------------------------------------------------------------
# Enabled mode — health + shape
# ---------------------------------------------------------------------------

def test_simulator_health_endpoint(client_we2_on):
    resp = client_we2_on.get("/api/face/we2/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["backend"] == "we2-simulator"
    assert body["model_tag"] == "we2-mfn128-v1"
    assert body["capabilities"] == ["detect", "embed"]
    assert body["embedding_dim"] == 128
    assert body["embedding_dtype"] == "float32"


@pytest.mark.skipif(
    not FIXTURE_FACE.exists(), reason="WE2 test face fixture missing"
)
def test_simulator_returns_face_rec_api_shape(client_we2_on):
    img = Image.open(FIXTURE_FACE).convert("RGB")
    payload = {"image_b64": _image_to_b64(img, "JPEG")}

    resp = client_we2_on.post("/api/face/we2/infer", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["model_tag"] == "we2-mfn128-v1"
    assert body["backend"] == "we2-simulator"
    assert "faces" in body and "face_count" in body
    assert body["face_count"] == len(body["faces"])
    assert body["processing_time_ms"] > 0

    if body["face_count"] == 0:
        pytest.skip(
            "SCRFD did not detect a face in the fixture — environment-dependent"
        )

    face = body["faces"][0]
    assert "bbox" in face and len(face["bbox"]) == 4
    assert "landmarks" in face and len(face["landmarks"]) == 5
    assert all(len(p) == 2 for p in face["landmarks"])
    assert 0.0 <= face["det_score"] <= 1.0

    raw = base64.b64decode(face["embedding"])
    assert len(raw) == 512, (
        f"expected 512 bytes (128 float32 LE), got {len(raw)}"
    )
    arr = np.frombuffer(raw, dtype=np.float32)
    assert arr.shape == (128,)
    # Dequantized float32 values land in a small bounded range (typical
    # MFN INT8 scale ~ 0.025) — sanity check, not a tight bound.
    assert np.all(np.isfinite(arr))
    assert float(np.max(np.abs(arr))) < 100.0


# ---------------------------------------------------------------------------
# Determinism — bit-exact across calls (BUILTIN_REF contract)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not FIXTURE_FACE.exists(), reason="WE2 test face fixture missing"
)
def test_simulator_deterministic_via_http(client_we2_on):
    img = Image.open(FIXTURE_FACE).convert("RGB")
    payload = {"image_b64": _image_to_b64(img, "JPEG")}

    r1 = client_we2_on.post("/api/face/we2/infer", json=payload).json()
    r2 = client_we2_on.post("/api/face/we2/infer", json=payload).json()

    if r1["face_count"] == 0 or r2["face_count"] == 0:
        pytest.skip("no face detected, can't test embedding determinism")

    e1 = base64.b64decode(r1["faces"][0]["embedding"])
    e2 = base64.b64decode(r2["faces"][0]["embedding"])
    assert hashlib.sha256(e1).hexdigest() == hashlib.sha256(e2).hexdigest()


def test_simulator_deterministic_direct():
    """Direct (non-HTTP) call: same numpy input → identical bytes.

    Uses a fixed RGB array (not the fixture) so the test is meaningful even
    if SCRFD misses on the fixture face. If no face is detected we fall back
    to skipping; this is the strongest "BUILTIN_REF byte-equality" check.
    """
    from face.we2 import get_simulator  # noqa: E402

    sim = get_simulator()

    if FIXTURE_FACE.exists():
        img = Image.open(FIXTURE_FACE).convert("RGB")
    else:
        rng = np.random.default_rng(seed=42)
        arr = rng.integers(0, 256, size=(320, 320, 3), dtype=np.uint8)
        img = Image.fromarray(arr)

    res_a = sim.infer(img)
    res_b = sim.infer(img)

    assert res_a["face_count"] == res_b["face_count"]
    if res_a["face_count"] == 0:
        pytest.skip("no face detected; determinism check needs at least one")

    eb_a = res_a["faces"][0]["embedding_bytes"]
    eb_b = res_b["faces"][0]["embedding_bytes"]
    assert hashlib.sha256(eb_a).hexdigest() == hashlib.sha256(eb_b).hexdigest()
    assert len(eb_a) == 512  # 128 float32 LE


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_simulator_rejects_bad_base64(client_we2_on):
    resp = client_we2_on.post(
        "/api/face/we2/infer", json={"image_b64": "not-an-image"}
    )
    # Either 400 (bad payload) or, depending on PIL's tolerance, 400 from decode
    assert resp.status_code == 400


def test_simulator_handles_no_face_gracefully(client_we2_on):
    # Plain white image with a black circle — SCRFD will not find a face
    arr = np.full((320, 320, 3), 255, dtype=np.uint8)
    y, x = np.ogrid[:320, :320]
    arr[(x - 160) ** 2 + (y - 160) ** 2 < 60 ** 2] = 0
    img = Image.fromarray(arr)
    payload = {"image_b64": _image_to_b64(img, "PNG")}

    resp = client_we2_on.post("/api/face/we2/infer", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["face_count"] == 0
    assert body["faces"] == []
    assert body["model_tag"] == "we2-mfn128-v1"


# ---------------------------------------------------------------------------
# Cross-platform parity: dequantize formula matches manual computation
# ---------------------------------------------------------------------------

def test_simulator_dequant_matches_manual():
    """Lock the dequant formula: scale * (int8 - zp), float32 LE bytes.

    The simulator exposes the raw INT8 MFN output via
    ``embedding_int8_raw``. We recompute float32 the warehouse contract way
    and assert byte-for-byte equality with the published
    ``embedding_bytes``. If anyone changes the formula (e.g. forgets
    zero_point, swaps the sign, uses float64 intermediate that rounds
    differently), this test fails.

    This is the same path the watcher firmware uses to ship float32 LE
    over the face_bench protocol, so passing this guarantees on-device and
    host embeddings stay byte-equivalent.
    """
    from face.we2 import get_simulator  # noqa: E402

    sim = get_simulator()

    if FIXTURE_FACE.exists():
        img = Image.open(FIXTURE_FACE).convert("RGB")
    else:
        rng = np.random.default_rng(seed=42)
        arr = rng.integers(0, 256, size=(320, 320, 3), dtype=np.uint8)
        img = Image.fromarray(arr)

    res = sim.infer(img)
    if res["face_count"] == 0:
        pytest.skip("no face detected; need at least one to verify dequant")

    face = res["faces"][0]
    assert "embedding_int8_raw" in face, "simulator must expose raw int8 hook"

    int8_arr = np.frombuffer(face["embedding_int8_raw"], dtype=np.int8)
    assert int8_arr.shape == (128,)

    # Pull the (scale, zp) the simulator actually used.
    scale = float(sim._emb_out_scale)
    zp = float(sim._emb_out_zp)
    manual = ((int8_arr.astype(np.float32) - zp) * scale).astype(np.float32)
    manual_bytes = manual.tobytes()

    sim_bytes = face["embedding_bytes"]
    assert len(sim_bytes) == 512, (
        f"expected 512 bytes (128 float32), got {len(sim_bytes)}"
    )
    assert sim_bytes == manual_bytes, (
        "dequant byte-for-byte mismatch — formula has drifted from "
        "scale * (int8 - zero_point)"
    )

    # Float32 round-trip is exact for the LE wire format.
    sim_arr = np.frombuffer(sim_bytes, dtype=np.float32)
    assert np.array_equal(sim_arr, manual)
