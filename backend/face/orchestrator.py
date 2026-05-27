"""Orchestration: load config, capture, infer, match, log decision."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, and_

from db import get_engine
from metadata import tenant_face_config, tenant_face_operation_rules

from . import endpoint_client
from .endpoint_client import FaceEndpointError
from .matcher import topk_match
from .models import Decision, FaceConfig, FaceRule

logger = logging.getLogger("warehouse.face")


# ── data access helpers ──

def _load_config(conn, tenant_id: int) -> Optional[FaceConfig]:
    """Phase 2b: read via SQLAlchemy Core. ``conn`` retained for signature
    compatibility but unused here (SA reads share the same DB)."""
    stmt = select(
        tenant_face_config.c.tenant_id,
        tenant_face_config.c.enabled,
        tenant_face_config.c.mode,
        tenant_face_config.c.endpoint,
        tenant_face_config.c.auth_token,
        tenant_face_config.c.embedding_model_tag,
        tenant_face_config.c.min_confidence,
    ).where(tenant_face_config.c.tenant_id == tenant_id)
    with get_engine().connect() as sa_conn:
        row = sa_conn.execute(stmt).first()
    if not row:
        return None
    return FaceConfig(
        tenant_id=row.tenant_id,
        enabled=bool(row.enabled),
        mode=row.mode,
        endpoint=row.endpoint,
        auth_token=row.auth_token,
        embedding_model_tag=row.embedding_model_tag,
        min_confidence=float(row.min_confidence or 0.65),
    )


def _parse_id_list(raw) -> List[int]:
    """Accept list | str | None. SA returns JSON columns already decoded as
    Python lists; the legacy sqlite3 path returned the raw JSON string."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        try:
            return [int(x) for x in raw]
        except Exception:
            return []
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return [int(x) for x in v] if isinstance(v, list) else []
        except Exception:
            return []
    return []


def _pick_rule(conn, tenant_id: int, warehouse_id: Optional[int], operation: str) -> Optional[FaceRule]:
    """Warehouse-specific rule wins over the tenant default (warehouse_id IS NULL).

    Phase 2b: read via SQLAlchemy Core. ``conn`` retained for signature
    compatibility but unused here.
    """
    cols = (
        tenant_face_operation_rules.c.id,
        tenant_face_operation_rules.c.tenant_id,
        tenant_face_operation_rules.c.warehouse_id,
        tenant_face_operation_rules.c.operation,
        tenant_face_operation_rules.c.require_face,
        tenant_face_operation_rules.c.allowed_subject_ids,
        tenant_face_operation_rules.c.min_confidence_override,
    )
    with get_engine().connect() as sa_conn:
        if warehouse_id is not None:
            stmt = select(*cols).where(
                and_(
                    tenant_face_operation_rules.c.tenant_id == tenant_id,
                    tenant_face_operation_rules.c.operation == operation,
                    tenant_face_operation_rules.c.warehouse_id == warehouse_id,
                )
            ).limit(1)
            row = sa_conn.execute(stmt).first()
            if row:
                return _row_to_rule(row)
        stmt = select(*cols).where(
            and_(
                tenant_face_operation_rules.c.tenant_id == tenant_id,
                tenant_face_operation_rules.c.operation == operation,
                tenant_face_operation_rules.c.warehouse_id.is_(None),
            )
        ).limit(1)
        row = sa_conn.execute(stmt).first()
    return _row_to_rule(row) if row else None


def _row_to_rule(row) -> FaceRule:
    return FaceRule(
        id=row.id,
        tenant_id=row.tenant_id,
        warehouse_id=row.warehouse_id,
        operation=row.operation,
        require_face=bool(row.require_face),
        allowed_subject_ids=_parse_id_list(row.allowed_subject_ids),
        min_confidence_override=row.min_confidence_override,
    )


def _log_decision(
    conn,
    *,
    request_id: Optional[str],
    user_id: int,
    matched_subject_id: Optional[int],
    tenant_id: int,
    warehouse_id: Optional[int],
    operation: str,
    confidence: Optional[float],
    decision: str,
    failure_reason: Optional[str],
) -> None:
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO face_auth_logs
                (request_id, user_id, matched_subject_id, tenant_id, warehouse_id,
                 operation, confidence, decision, failure_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                user_id,
                matched_subject_id,
                tenant_id,
                warehouse_id,
                operation,
                confidence,
                decision,
                failure_reason,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("failed to write face_auth_logs")


# ── public API ──

async def enroll_face(
    conn,
    *,
    subject_id: int,
    tenant_id: int,
    images_b64: Optional[List[str]] = None,
    precomputed: Optional[List[dict]] = None,
    applies_to_warehouse_ids: Optional[List[int]] = None,
    enrolled_by: Optional[int] = None,
) -> dict:
    """Persist face enrollments for a subject.

    Two ingress paths (pick one — pass both raises):

    * ``images_b64``: server-side path. Warehouse posts each image to the
      tenant's ``/infer`` (face_rec_api Hailo/Jetson/RKNN) and stores the
      returned embedding.
    * ``precomputed``: device-side path (e.g. Himax WE2). Each item is
      ``{"embedding_bytes": <bytes>, "model_tag": <str>}`` already computed
      on the device's NPU; warehouse just persists it. No /infer call,
      and ``cfg.endpoint`` is not required.

    Returns: ``{count, ids}``.
    Raises FaceEndpointError on config / subject / endpoint failures.
    """
    if images_b64 and precomputed:
        raise FaceEndpointError("ambiguous_enroll_input")
    images_b64 = images_b64 or []
    precomputed = precomputed or []
    if not images_b64 and not precomputed:
        return {"count": 0, "ids": []}

    cfg = _load_config(conn, tenant_id)
    if images_b64:
        # Server-side inference requires a configured endpoint.
        if cfg is None or not cfg.endpoint:
            raise FaceEndpointError("endpoint_not_configured")

    # Verify the subject belongs to this tenant
    cur = conn.cursor()
    cur.execute(
        "SELECT tenant_id FROM face_subjects WHERE id = ?",
        (subject_id,),
    )
    row = cur.fetchone()
    if not row or int(row["tenant_id"]) != int(tenant_id):
        raise FaceEndpointError("subject_not_in_tenant")

    applies_raw = json.dumps(applies_to_warehouse_ids) if applies_to_warehouse_ids else None
    inserted_ids: List[int] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for img in images_b64:
        result = await endpoint_client.infer(cfg, img)
        cur.execute(
            """
            INSERT INTO face_enrollments
                (subject_id, tenant_id, model_tag, embedding, source_image_b64,
                 applies_to_warehouse_ids, is_active, enrolled_at, enrolled_by)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (subject_id, tenant_id, result["model_tag"], result["embedding"],
             img, applies_raw, now, enrolled_by),
        )
        inserted_ids.append(cur.lastrowid)

    for item in precomputed:
        emb_bytes = item.get("embedding_bytes")
        model_tag = item.get("model_tag") or (cfg.embedding_model_tag if cfg else None) or "unknown"
        if not emb_bytes:
            raise FaceEndpointError("precomputed_missing_embedding")
        cur.execute(
            """
            INSERT INTO face_enrollments
                (subject_id, tenant_id, model_tag, embedding, source_image_b64,
                 applies_to_warehouse_ids, is_active, enrolled_at, enrolled_by)
            VALUES (?, ?, ?, ?, NULL, ?, 1, ?, ?)
            """,
            (subject_id, tenant_id, model_tag, emb_bytes,
             applies_raw, now, enrolled_by),
        )
        inserted_ids.append(cur.lastrowid)

    conn.commit()
    return {"count": len(inserted_ids), "ids": inserted_ids}


async def verify_mcp_face(
    conn,
    *,
    tenant_id: int,
    user_id: int,
    warehouse_id: Optional[int],
    operation: str,
    image_b64: str = "",
    embedding_bytes: Optional[bytes] = None,
    embedding_model_tag: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Decision:
    """Short-circuit verification ladder for an MCP tool call.

    Order: config -> rule -> obtain embedding -> match -> threshold ->
    allow-list. The matched **subject** is the authorization unit; the
    calling system user is just identified for audit purposes.

    Two ingress paths for the embedding (pick one):

    * ``image_b64``: warehouse calls the tenant's ``/infer`` (face_rec_api).
    * ``embedding_bytes`` + ``embedding_model_tag``: device already ran
      inference on its own NPU (e.g. Himax WE2) and posts the result
      directly. ``cfg.endpoint`` is not consulted on this path.
    """
    cfg = _load_config(conn, tenant_id)
    if cfg is None or not cfg.enabled:
        decision = Decision(status="skipped", failure_reason="feature_disabled")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    rule = _pick_rule(conn, tenant_id, warehouse_id, operation)
    if rule is None or not rule.require_face:
        decision = Decision(status="skipped", failure_reason="rule_not_required")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    # Obtain embedding: either supplied by device (WE2 path) or via /infer.
    if embedding_bytes:
        model_tag = embedding_model_tag or cfg.embedding_model_tag or "unknown"
        query_bytes = embedding_bytes
    elif image_b64:
        try:
            result = await endpoint_client.infer(cfg, image_b64)
        except FaceEndpointError as e:
            reason = str(e) if str(e) else "endpoint_unreachable"
            decision = Decision(status="deny", failure_reason=reason)
            _log_decision(
                conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
                tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
                confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
            )
            return decision
        model_tag = result["model_tag"]
        query_bytes = result["embedding"]
    else:
        decision = Decision(status="deny", failure_reason="no_image_provided")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    matches = topk_match(
        conn,
        tenant_id=tenant_id,
        warehouse_id=warehouse_id,
        model_tag=model_tag,
        query_emb_bytes=query_bytes,
        k=1,
    )
    if not matches:
        decision = Decision(status="deny", failure_reason="no_match")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    best = matches[0]
    threshold = rule.min_confidence_override if rule.min_confidence_override is not None else cfg.min_confidence

    if best.confidence < threshold:
        decision = Decision(
            status="deny", failure_reason="low_confidence",
            confidence=best.confidence, matched_subject_id=best.subject_id,
        )
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=best.subject_id,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=best.confidence, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    if rule.allowed_subject_ids and best.subject_id not in rule.allowed_subject_ids:
        decision = Decision(
            status="deny", failure_reason="not_in_allow_list",
            confidence=best.confidence, matched_subject_id=best.subject_id,
        )
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_subject_id=best.subject_id,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=best.confidence, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    decision = Decision(
        status="pass", failure_reason=None,
        confidence=best.confidence, matched_subject_id=best.subject_id,
    )
    _log_decision(
        conn, request_id=request_id, user_id=user_id, matched_subject_id=best.subject_id,
        tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
        confidence=best.confidence, decision=decision.status, failure_reason=None,
    )
    return decision
