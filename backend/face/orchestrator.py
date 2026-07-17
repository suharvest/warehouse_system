"""Orchestration: load config, capture, infer, match, log decision."""
from __future__ import annotations

import json
import logging
import math
import time
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

# 「仅首次验证（之后免验）」= verify_mode 'session' 的按对话缓存。设备每轮对话把
# conv_seq 递增（CaptureCurrentSpeaker）；同一对话内首笔操作走 fresh=1 现场验证
# （含屏幕预览），之后同 conv_seq 的操作直接返回缓存（免验、不再拍照/预览）。
# 键：设备 ip:port（一连接一设备）。进程内内存，重启即失效（首笔重验，安全）。
# TTL 上限兜底：即使一直不换对话，超过 N 秒也强制重验，防长会话无限免验。
_verify_once_cache: dict = {}
_VERIFY_ONCE_TTL_S = 600  # 10 分钟安全上限


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
        tenant_face_config.c.verify_mode,
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
        verify_mode=row.verify_mode or "interface",
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


def _resolve_speaker_subject(
    conn,
    *,
    tenant_id: int,
    speaker_subject_id: Optional[int],
    speaker_name: Optional[str],
) -> Optional[int]:
    """Resolve the session-mode speaker to a tenant-scoped face_subjects.id.

    Precedence is strict, not a fallback chain: when ``speaker_subject_id`` is
    supplied it is the ONLY authority — if it fails to resolve (wrong tenant /
    deactivated) we return None (→ deny), we do NOT fall back to a name lookup.
    Falling back would let a device-reported id for tenant B's subject silently
    match a same-named subject in the caller's tenant. Name lookup is used only
    when no id was provided at all. Both paths require an active subject.
    Returns None when nothing resolves (e.g. device had no valid match).
    """
    cur = conn.cursor()
    if speaker_subject_id is not None:
        cur.execute(
            "SELECT id FROM face_subjects "
            "WHERE id = ? AND tenant_id = ? AND is_active = 1",
            (speaker_subject_id, tenant_id),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])
        # id given but unresolved → hard deny; never degrade to name matching.
        return None
    if speaker_name:
        cur.execute(
            "SELECT id FROM face_subjects "
            "WHERE tenant_id = ? AND name = ? AND is_active = 1 "
            "ORDER BY id ASC LIMIT 2",
            (tenant_id, speaker_name),
        )
        rows = cur.fetchall()
        # 同租户无姓名唯一约束：查到多条同名 → 无法确定是谁 → deny（绝不任取第一条，
        # 否则设备只回 name 时可能把甲授权成同名的乙）。恰好一条才放行。
        if len(rows) == 1:
            return int(rows[0]["id"])
    return None


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
        # Server-side inference: local mode runs the bundled WE2 simulator
        # in-process (no endpoint); any other mode needs a configured endpoint.
        if cfg is None:
            raise FaceEndpointError("endpoint_not_configured")
        if cfg.mode != "local" and not cfg.endpoint:
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
    speaker_subject_id: Optional[int] = None,
    speaker_name: Optional[str] = None,
    pull_device: "Optional[object]" = None,
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

    # ── Session mode (backend-direct device pull, B 方案) ────────────────
    # The identity is NOT taken from LLM-forwarded speaker_* params (those are
    # LLM-visible → forgeable by prompt injection). Instead the backend pulls it
    # straight from the physical device over the LAN (fresh capture each op), so
    # the trust root is "the device's HTTP response", not "the model's word".
    # speaker_subject_id / speaker_name are ignored here on purpose.
    # Anything short of a live, resolvable, allowed identity → deny (fail-closed).
    if cfg.verify_mode == "session":
        def _session_deny(reason: str, matched=None) -> Decision:
            d = Decision(status="deny", failure_reason=reason, matched_subject_id=matched)
            _log_decision(
                conn, request_id=request_id, user_id=user_id, matched_subject_id=matched,
                tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
                confidence=None, decision=d.status, failure_reason=d.failure_reason,
            )
            return d

        if pull_device is None:
            # No device resolvable for this API key → cannot verify → deny.
            return _session_deny("device_unresolved")

        from . import device_pull
        dev_key = f"{pull_device.ip}:{pull_device.port}"

        def _finish_pass(matched, confidence, reason):
            decision = Decision(
                status="pass", failure_reason=reason,
                matched_subject_id=matched, confidence=confidence,
            )
            _log_decision(
                conn, request_id=request_id, user_id=user_id, matched_subject_id=matched,
                tenant_id=tenant_id, warehouse_id=warehouse_id, operation=operation,
                confidence=confidence, decision=decision.status,
                failure_reason=decision.failure_reason,
            )
            return decision

        # ── 仅首次（verify-once-per-conversation）缓存命中检查 ──────────────
        # 同一对话内首笔操作走下面的 fresh=1 现场验证（含屏幕预览）；之后同 conv_seq
        # 的操作直接读缓存放行（免验、不再拍照）。用 fresh=0（零硬件动作）廉价读当前
        # conv_seq 判断是否还是那轮对话。
        cached = _verify_once_cache.get(dev_key)
        if cached is not None and (time.time() - cached["ts"]) <= _VERIFY_ONCE_TTL_S:
            ident0 = await device_pull.pull_current_speaker(pull_device, fresh=0)
            cur_seq = ident0.get("conv_seq") if isinstance(ident0, dict) else None
            if isinstance(cur_seq, int) and cur_seq == cached["conv_seq"]:
                # 同一对话，已验过 → 仅复查该 subject 仍活跃 + 仍在白名单（规则可能变），
                # 不再现场拍照。停用/移出白名单则作废缓存、走下面重验。
                matched = _resolve_speaker_subject(
                    conn, tenant_id=tenant_id,
                    speaker_subject_id=cached["subject_id"], speaker_name=None,
                )
                if matched is not None and (
                    not rule.allowed_subject_ids or matched in rule.allowed_subject_ids
                ):
                    return _finish_pass(matched, None, "session_cached")
                _verify_once_cache.pop(dev_key, None)

        # ── 现场验证（首笔 / 缓存未命中 / 缓存失效）：fresh=1 现拍 + 预览 ──────
        ident = await device_pull.pull_current_speaker(pull_device, fresh=1)
        # 严格判定：valid 必须是布尔 True（truthy 字符串/数字不算），否则一律 deny。
        if not isinstance(ident, dict) or ident.get("valid") is not True:
            # HTTP error / timeout / busy / no face / stranger → deny.
            return _session_deny("device_no_identity")

        # Resolve the device-reported identity to a tenant-scoped subject. Prefer
        # subject_id (local mode); fall back to name only when the device gave no
        # id (lan mode is name-keyed, subject_id=0). Same strict rule as elsewhere:
        # a provided id that fails to resolve does NOT degrade to name matching.
        dev_sid = ident.get("subject_id") or None
        matched = _resolve_speaker_subject(
            conn, tenant_id=tenant_id,
            speaker_subject_id=dev_sid,
            speaker_name=(ident.get("name") if not dev_sid else None),
        )
        if matched is None:
            return _session_deny("speaker_unresolved")
        if rule.allowed_subject_ids and matched not in rule.allowed_subject_ids:
            return _session_deny("not_in_allow_list", matched)

        raw_conf = ident.get("similarity")
        confidence = (
            float(raw_conf)
            if isinstance(raw_conf, (int, float))
            and not isinstance(raw_conf, bool)
            and math.isfinite(raw_conf)
            else None
        )
        # 缓存本轮对话的验证结果，供同 conv_seq 的后续操作免验。
        conv_seq = ident.get("conv_seq")
        if isinstance(conv_seq, int):
            _verify_once_cache[dev_key] = {
                "conv_seq": conv_seq, "subject_id": matched, "ts": time.time(),
            }
        return _finish_pass(matched, confidence, "session_verified")

    # ── Interface mode (default, fail-closed) ───────────────────────────
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
