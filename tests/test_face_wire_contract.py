"""Wire-format contract tests for the face-verify path.

These are NOT behavior tests — `test_face.py` covers the orchestrator
decision ladder already. The purpose of *this* file is to lock in the
*format* expected on the wire between:

  xiaozhi runtime  →  warehouse MCP  →  matcher.py

So that any future refactor that silently changes the embedding dtype,
length, or normalization assumption gets caught immediately.

The contract this file pins (read this when in doubt):

  1. Embedding storage format: raw float32 LE bytes. The native dim is
     128 → 512 bytes. int8 / float16 / int8-quantized are all WRONG.
  2. Length must be a multiple of 4 (float32 stride). Non-aligned blobs
     return None, never crash.
  3. L2 normalization: the matcher normalizes on the fly inside
     ``_cosine``. Unnormalized embeddings match identically. The
     device MAY normalize but is not required to. This is why the
     original handoff doc's "DO NOT normalize" rule was a false alarm.
  4. Degenerate (zero-norm) vector: cosine returns 0.0, never raises.

If any of these break, fix the *caller* (xiaozhi / handoff doc), not
the matcher — the matcher format is the source of truth.

The `topk_match`-level model_tag filtering invariant is tested in
test_face.py via the orchestrator path; this file only exercises the
pure (no-DB) helpers.
"""
from __future__ import annotations

import os
import sys

import numpy as np

# Make the `backend` package importable when running from repo root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.face.matcher import _bytes_to_vec, _cosine  # noqa: E402


# ── Embedding wire format ────────────────────────────────────────────

def test_float32_le_is_the_wire_format():
    vec = [0.1, 0.2, 0.3]
    raw = np.asarray(vec, dtype=np.float32).tobytes()
    assert len(raw) == 12  # 3 floats × 4 bytes
    out = _bytes_to_vec(raw)
    assert out is not None
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, vec, rtol=1e-6)


def test_128_float32_embedding_is_512_bytes():
    """The WE2 / face_rec_api native output: 128 floats = 512 bytes."""
    vec = np.random.RandomState(0).randn(128).astype(np.float32)
    raw = vec.tobytes()
    assert len(raw) == 512
    out = _bytes_to_vec(raw)
    assert out is not None
    assert out.shape == (128,)


def test_int8_is_NOT_the_format():
    """Document the pitfall: a 128-int8 buffer is the wrong length.

    `int8[128]` is 128 bytes — `_bytes_to_vec` would happily decode
    those 128 bytes as 32 float32 values (junk magnitudes, junk dim).
    This test exists so that anyone tempted to "compress" embeddings
    to int8 (per the old handoff doc) sees the size mismatch
    immediately.
    """
    vec_int8 = np.random.RandomState(0).randint(-128, 128, size=128, dtype=np.int8)
    raw = vec_int8.tobytes()
    assert len(raw) == 128
    decoded = _bytes_to_vec(raw)
    assert decoded is not None
    assert decoded.shape == (32,), (
        "128 int8 bytes get decoded as 32 float32 — wrong dim, never matches"
    )


def test_non_multiple_of_4_bytes_returns_none():
    """Trailing junk that breaks the float32 stride is rejected."""
    raw = np.asarray([1.0, 2.0, 3.0], dtype=np.float32).tobytes() + b"\x00\x00"
    assert _bytes_to_vec(raw) is None


def test_empty_bytes_returns_none():
    assert _bytes_to_vec(b"") is None
    assert _bytes_to_vec(None) is None  # type: ignore[arg-type]


# ── Normalization invariant ─────────────────────────────────────────

def test_unnormalized_embedding_scores_same_as_normalized():
    """Cosine is scale-invariant. Devices may skip L2 norm without losing accuracy."""
    rs = np.random.RandomState(42)
    a = rs.randn(128).astype(np.float32)
    b = (a * 1000.0).astype(np.float32)  # huge magnitude difference
    assert abs(_cosine(a, b) - 1.0) < 1e-4


def test_zero_vector_returns_zero_not_nan():
    """Defensive: a degenerate (all-zero) embedding doesn't divide-by-zero."""
    a = np.zeros(128, dtype=np.float32)
    b = np.ones(128, dtype=np.float32)
    assert _cosine(a, b) == 0.0
    assert _cosine(b, a) == 0.0


def test_shape_mismatch_returns_zero():
    """Dim mismatch (e.g., 128 vs 32 from the int8-pitfall above) doesn't crash."""
    a = np.ones(128, dtype=np.float32)
    b = np.ones(32, dtype=np.float32)
    assert _cosine(a, b) == 0.0


# ── Round-trip via the storage layer ────────────────────────────────

def test_round_trip_preserves_cosine_to_machine_precision():
    """Pack → bytes → unpack → cosine ≈ 1.0 against the original."""
    rs = np.random.RandomState(7)
    vec = rs.randn(128).astype(np.float32)
    decoded = _bytes_to_vec(vec.tobytes())
    assert decoded is not None
    assert abs(_cosine(vec, decoded) - 1.0) < 1e-6
