"""Numpy cosine matcher over face_enrollments.

Phase 1 keeps it simple: full table scan filtered by
(tenant_id, model_tag, is_active, warehouse-applies). For < 10k
enrollments per tenant this is O(N) and fine; we'll swap in FAISS or
sqlite-vec later if it becomes a bottleneck.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

import numpy as np

from .models import Match

logger = logging.getLogger("warehouse.face")


def _bytes_to_vec(b: bytes) -> Optional[np.ndarray]:
    """Decode an embedding blob to a float32 numpy vector.

    Embeddings are stored as raw float32 little-endian bytes. Returns
    None if the buffer cannot be decoded into a non-empty vector.
    """
    if not b:
        return None
    try:
        # length must be a multiple of 4 for float32
        if len(b) % 4 != 0:
            return None
        v = np.frombuffer(b, dtype=np.float32)
        if v.size == 0:
            return None
        return v
    except Exception:
        return None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _applies_to_warehouse(applies_to_raw: Optional[str], warehouse_id: Optional[int]) -> bool:
    """An enrollment applies if applies_to_warehouse_ids is NULL/empty
    (= all warehouses) or contains the requested warehouse_id."""
    if not applies_to_raw:
        return True
    try:
        ids = json.loads(applies_to_raw)
    except Exception:
        return True
    if not ids:
        return True
    if warehouse_id is None:
        # Strict: enrollment is scoped, no warehouse context => skip
        return False
    return int(warehouse_id) in [int(x) for x in ids]


def topk_match(
    conn,
    tenant_id: int,
    warehouse_id: Optional[int],
    model_tag: str,
    query_emb_bytes: bytes,
    k: int = 1,
) -> List[Match]:
    """Return the top-k matches for the given query embedding.

    Filters enrollments by (tenant_id, model_tag, is_active=1) and the
    warehouse-scope rule. Empty list if no candidates or the query
    cannot be decoded.
    """
    q = _bytes_to_vec(query_emb_bytes)
    if q is None:
        return []

    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, embedding, applies_to_warehouse_ids
        FROM face_enrollments
        WHERE tenant_id = ? AND model_tag = ? AND is_active = 1
        """,
        (tenant_id, model_tag),
    )
    scored: List[Match] = []
    for row in cur.fetchall():
        if not _applies_to_warehouse(row["applies_to_warehouse_ids"], warehouse_id):
            continue
        v = _bytes_to_vec(row["embedding"])
        if v is None:
            continue
        score = _cosine(q, v)
        scored.append(Match(enrollment_id=row["id"], user_id=row["user_id"], confidence=score))

    scored.sort(key=lambda m: m.confidence, reverse=True)
    return scored[:k]
