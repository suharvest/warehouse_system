"""Orchestration: load config, capture, infer, match, log decision."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from . import endpoint_client
from .endpoint_client import FaceEndpointError
from .matcher import topk_match
from .models import Decision, FaceConfig, FaceRule

logger = logging.getLogger("warehouse.face")


# ── data access helpers ──

def _load_config(conn, tenant_id: int) -> Optional[FaceConfig]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM tenant_face_config WHERE tenant_id = ?",
        (tenant_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return FaceConfig(
        tenant_id=row["tenant_id"],
        enabled=bool(row["enabled"]),
        mode=row["mode"],
        endpoint=row["endpoint"],
        auth_token=row["auth_token"],
        embedding_model_tag=row["embedding_model_tag"],
        min_confidence=float(row["min_confidence"] or 0.65),
    )


def _parse_id_list(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [int(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return []


def _pick_rule(conn, tenant_id: int, warehouse_id: Optional[int], operation: str) -> Optional[FaceRule]:
    """Warehouse-specific rule wins over the tenant default (warehouse_id IS NULL)."""
    cur = conn.cursor()
    if warehouse_id is not None:
        cur.execute(
            """
            SELECT * FROM tenant_face_operation_rules
            WHERE tenant_id = ? AND operation = ? AND warehouse_id = ?
            LIMIT 1
            """,
            (tenant_id, operation, warehouse_id),
        )
        row = cur.fetchone()
        if row:
            return _row_to_rule(row)
    cur.execute(
        """
        SELECT * FROM tenant_face_operation_rules
        WHERE tenant_id = ? AND operation = ? AND warehouse_id IS NULL
        LIMIT 1
        """,
        (tenant_id, operation),
    )
    row = cur.fetchone()
    return _row_to_rule(row) if row else None


def _row_to_rule(row) -> FaceRule:
    return FaceRule(
        id=row["id"],
        tenant_id=row["tenant_id"],
        warehouse_id=row["warehouse_id"],
        operation=row["operation"],
        require_face=bool(row["require_face"]),
        allowed_user_ids=_parse_id_list(row["allowed_user_ids"]),
        min_confidence_override=row["min_confidence_override"],
    )


def _log_decision(
    conn,
    *,
    request_id: Optional[str],
    user_id: int,
    matched_user_id: Optional[int],
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
                (request_id, user_id, matched_user_id, tenant_id, warehouse_id,
                 operation, confidence, decision, failure_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                user_id,
                matched_user_id,
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
    user_id: int,
    tenant_id: int,
    images_b64: List[str],
    applies_to_warehouse_ids: Optional[List[int]] = None,
    enrolled_by: Optional[int] = None,
) -> dict:
    """Run each image through endpoint /infer, persist embeddings.

    Returns: {count, ids}
    Raises FaceEndpointError if config missing / endpoint unreachable.
    """
    cfg = _load_config(conn, tenant_id)
    if cfg is None or not cfg.endpoint:
        raise FaceEndpointError("endpoint_not_configured")
    if not images_b64:
        return {"count": 0, "ids": []}

    applies_raw = json.dumps(applies_to_warehouse_ids) if applies_to_warehouse_ids else None
    inserted_ids: List[int] = []
    cur = conn.cursor()
    for img in images_b64:
        result = await endpoint_client.infer(cfg, img)
        cur.execute(
            """
            INSERT INTO face_enrollments
                (user_id, tenant_id, model_tag, embedding, source_image_b64,
                 applies_to_warehouse_ids, is_active, enrolled_at, enrolled_by)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                user_id,
                tenant_id,
                result["model_tag"],
                result["embedding"],
                img,
                applies_raw,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                enrolled_by,
            ),
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
    request_id: Optional[str] = None,
) -> Decision:
    """Short-circuit verification ladder for an MCP tool call.

    Order: config -> rule -> capture -> infer -> match -> threshold ->
    allow-list -> identity (matched.user_id == user_id). Always logs.
    """
    cfg = _load_config(conn, tenant_id)
    if cfg is None or not cfg.enabled:
        decision = Decision(status="skipped", failure_reason="feature_disabled")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_user_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    rule = _pick_rule(conn, tenant_id, warehouse_id, operation)
    if rule is None or not rule.require_face:
        decision = Decision(status="skipped", failure_reason="rule_not_required")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_user_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    # capture + infer
    try:
        snap = await endpoint_client.capture(cfg)
        result = await endpoint_client.infer(cfg, snap["image_b64"])
    except FaceEndpointError as e:
        reason = str(e) if str(e) else "endpoint_unreachable"
        decision = Decision(status="deny", failure_reason=reason)
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_user_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    matches = topk_match(
        conn,
        tenant_id=tenant_id,
        warehouse_id=warehouse_id,
        model_tag=result["model_tag"],
        query_emb_bytes=result["embedding"],
        k=1,
    )
    if not matches:
        decision = Decision(status="deny", failure_reason="no_match")
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_user_id=None,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=None, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    best = matches[0]
    threshold = rule.min_confidence_override if rule.min_confidence_override is not None else cfg.min_confidence

    if best.confidence < threshold:
        decision = Decision(
            status="deny", failure_reason="low_confidence",
            confidence=best.confidence, matched_user_id=best.user_id,
        )
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_user_id=best.user_id,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=best.confidence, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    if rule.allowed_user_ids and best.user_id not in rule.allowed_user_ids:
        decision = Decision(
            status="deny", failure_reason="not_in_allow_list",
            confidence=best.confidence, matched_user_id=best.user_id,
        )
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_user_id=best.user_id,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=best.confidence, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    if best.user_id != user_id:
        decision = Decision(
            status="deny", failure_reason="user_mismatch",
            confidence=best.confidence, matched_user_id=best.user_id,
        )
        _log_decision(
            conn, request_id=request_id, user_id=user_id, matched_user_id=best.user_id,
            tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
            confidence=best.confidence, decision=decision.status, failure_reason=decision.failure_reason,
        )
        return decision

    decision = Decision(
        status="pass", failure_reason=None,
        confidence=best.confidence, matched_user_id=best.user_id,
    )
    _log_decision(
        conn, request_id=request_id, user_id=user_id, matched_user_id=best.user_id,
        tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
        confidence=best.confidence, decision=decision.status, failure_reason=None,
    )
    return decision
