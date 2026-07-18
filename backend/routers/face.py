"""Face-recognition admin/MCP routes (extracted from app.py — Phase 1, task #5).

All 16 routes keep their full literal ``/api/face/...`` path (the router
is mounted without a prefix) so that the snapshot in
``tests/fixtures/route_inventory.json`` stays byte-for-byte identical.

The face routers depend only on shared primitives from ``deps.py`` and
on face submodules in ``backend/face/``; nothing here imports from
``app.py`` so we avoid a circular-import on FastAPI app boot.
"""
from datetime import datetime
from typing import List, Optional
import base64 as _face_base64  # retained for parity with the previous in-line imports
import json as _face_json
import os as _os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy import func as _sa_func

from db import get_engine
from deps import (
    Action,
    CurrentUser,
    Resource,
    get_db,
    load_or_404,
    require_permission,
    resolve_warehouse_id,
)
from metadata import (
    face_auth_logs as _t_face_auth_logs,
    face_enrollments as _t_face_enrollments,
    face_subjects as _t_face_subjects,
    tenant_face_config as _t_tenant_face_config,
    tenant_face_operation_rules as _t_tenant_face_rules,
)

router = APIRouter()


# ============ Face Recognition Management APIs ============
# Phase 1: 仅对 MCP tool 调用生效，HTTP 出入库端点不受影响。
# 全局 admin (tenant_id=NULL) 可显式指定 ?tenant_id=N，否则默认使用 current_user 的租户。

class FaceConfigPayload(BaseModel):
    enabled: bool = False
    mode: Optional[str] = None
    endpoint: Optional[str] = None
    auth_token: Optional[str] = None
    embedding_model_tag: Optional[str] = None
    min_confidence: float = 0.65
    greeting_enabled: bool = False
    # 人脸验证频率（与推理拓扑 mode 正交，只控制会话缓存）：
    #   'always'（默认，每次操作都验证）或 'session'（首验通过后本会话免验）。
    verify_frequency: Optional[str] = None
    # DEPRECATED: 旧客户端兼容入参。未传 verify_frequency 时按
    # session→session / interface→always 映射；两者都传时 verify_frequency 优先。
    verify_mode: Optional[str] = None

class FaceRulePayload(BaseModel):
    warehouse_id: Optional[int] = None
    operation: str
    require_face: bool = False
    allowed_subject_ids: Optional[List[int]] = None
    min_confidence_override: Optional[float] = None

class FacePrecomputedEmbedding(BaseModel):
    """Embedding already computed on-device (e.g. Himax WE2 NPU)."""
    embedding_b64: str
    model_tag: Optional[str] = None


class FaceEnrollmentPayload(BaseModel):
    subject_id: int
    # Server-inference path: warehouse calls /infer for each image.
    images_b64: List[str] = Field(default_factory=list)
    # Device-inference path: caller already has the embedding (e.g. WE2).
    # Mutually exclusive with images_b64.
    embeddings: List[FacePrecomputedEmbedding] = Field(default_factory=list)
    applies_to_warehouse_ids: Optional[List[int]] = None

class FaceSubjectPayload(BaseModel):
    name: str
    employee_id: Optional[str] = None
    note: Optional[str] = None
    is_active: bool = True

class FaceTestConnectionPayload(BaseModel):
    endpoint: str
    auth_token: Optional[str] = None


def _get_recompute_status(tid: int):
    from face.orchestrator import get_recompute_status
    return get_recompute_status(tid)


def _face_resolve_tenant(current_user: 'CurrentUser', tenant_id: Optional[int]) -> int:
    """Resolve which tenant the request is acting on, with admin scope checks."""
    if current_user.tenant_id is None:
        # global admin
        if tenant_id is None:
            raise HTTPException(status_code=400, detail="全局 admin 必须指定 tenant_id")
        return tenant_id
    # tenant-scoped admin
    if tenant_id is not None and tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="无权访问其他租户")
    return current_user.tenant_id


@router.get("/api/face/config")
async def face_get_config(
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    # Phase 2f: SA Core read.
    with get_engine().connect() as sa_conn:
        row = sa_conn.execute(
            select(
                _t_tenant_face_config.c.tenant_id, _t_tenant_face_config.c.enabled,
                _t_tenant_face_config.c.mode, _t_tenant_face_config.c.endpoint,
                _t_tenant_face_config.c.auth_token, _t_tenant_face_config.c.embedding_model_tag,
                _t_tenant_face_config.c.min_confidence,
                _t_tenant_face_config.c.greeting_enabled,
                _t_tenant_face_config.c.verify_mode,
                _t_tenant_face_config.c.verify_frequency,
                _t_tenant_face_config.c.created_at,
                _t_tenant_face_config.c.updated_at,
            ).where(_t_tenant_face_config.c.tenant_id == tid)
        ).first()
    if not row:
        return {"tenant_id": tid, "enabled": False, "mode": None, "endpoint": None,
                "auth_token": None, "embedding_model_tag": None, "min_confidence": 0.65,
                "greeting_enabled": False, "verify_frequency": "always",
                "verify_mode": "interface",  # deprecated echo
                "recompute_status": _get_recompute_status(tid)}
    return {
        "tenant_id": row.tenant_id,
        "enabled": bool(row.enabled),
        "mode": row.mode,
        "endpoint": row.endpoint,
        "auth_token": row.auth_token,
        "embedding_model_tag": row.embedding_model_tag,
        "min_confidence": row.min_confidence,
        "greeting_enabled": bool(row.greeting_enabled),
        "verify_frequency": row.verify_frequency or "always",
        # DEPRECATED: 仅为旧客户端保留的回显；新代码请读 verify_frequency。
        "verify_mode": row.verify_mode or "interface",
        # 后台批量 embedding 补算进度（配置变更触发）：
        # {model_tag, done, total, running} 或 null（从未跑过）。UI 可选展示。
        "recompute_status": _get_recompute_status(tid),
        "created_at": row.created_at.strftime('%Y-%m-%d %H:%M:%S') if isinstance(row.created_at, datetime) else row.created_at,
        "updated_at": row.updated_at.strftime('%Y-%m-%d %H:%M:%S') if isinstance(row.updated_at, datetime) else row.updated_at,
    }


@router.put("/api/face/config")
async def face_put_config(
    payload: FaceConfigPayload,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    # 当前合法值只有 local/lan（与 tenant_face_config 的 CHECK 约束一致）。
    # 历史 API 调用方可能还在传 hello/jetson/custom/face_rec_api/we2 —— 这些
    # 都是"外部端点"形态，统一归一化为 lan，避免撞 DB 约束 500。
    _LEGACY_LAN_MODES = ("hello", "jetson", "custom", "face_rec_api", "we2")
    if payload.mode in _LEGACY_LAN_MODES:
        payload.mode = "lan"
    if payload.mode is not None and payload.mode not in ("local", "lan"):
        raise HTTPException(
            status_code=400,
            detail="mode 必须是 local 或 lan",
        )
    # verify_frequency 优先；旧客户端只传 verify_mode 时按迁移同规则映射
    # （session→session，interface→always）。两者都缺省 → 'always'。
    if payload.verify_frequency is not None:
        if payload.verify_frequency not in ("always", "session"):
            raise HTTPException(
                status_code=400,
                detail="verify_frequency 必须是 always 或 session",
            )
        frequency = payload.verify_frequency
    elif payload.verify_mode is not None:
        if payload.verify_mode not in ("session", "interface"):
            raise HTTPException(
                status_code=400,
                detail="verify_mode 必须是 session 或 interface",
            )
        frequency = "session" if payload.verify_mode == "session" else "always"
    else:
        frequency = "always"
    # deprecated 列反向同步写入（旧版本回滚后仍能读到一致语义）。
    legacy_verify_mode = "session" if frequency == "session" else "interface"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_engine().begin() as sa_conn:
        existing = sa_conn.execute(
            select(
                _t_tenant_face_config.c.id,
                _t_tenant_face_config.c.mode,
                _t_tenant_face_config.c.endpoint,
            ).where(_t_tenant_face_config.c.tenant_id == tid)
        ).first()
        if existing:
            sa_conn.execute(
                update(_t_tenant_face_config)
                .where(_t_tenant_face_config.c.tenant_id == tid)
                .values(
                    enabled=1 if payload.enabled else 0,
                    mode=payload.mode,
                    endpoint=payload.endpoint,
                    auth_token=payload.auth_token,
                    embedding_model_tag=payload.embedding_model_tag,
                    min_confidence=payload.min_confidence,
                    greeting_enabled=1 if payload.greeting_enabled else 0,
                    verify_frequency=frequency,
                    verify_mode=legacy_verify_mode,
                    updated_at=now,
                )
            )
        else:
            sa_conn.execute(
                insert(_t_tenant_face_config).values(
                    tenant_id=tid,
                    enabled=1 if payload.enabled else 0,
                    mode=payload.mode,
                    endpoint=payload.endpoint,
                    auth_token=payload.auth_token,
                    embedding_model_tag=payload.embedding_model_tag,
                    min_confidence=payload.min_confidence,
                    greeting_enabled=1 if payload.greeting_enabled else 0,
                    verify_frequency=frequency,
                    verify_mode=legacy_verify_mode,
                    created_at=now,
                    updated_at=now,
                )
            )
    # mode/endpoint 变化 → 当前生效 model_tag 可能变化 → 触发后台批量补算
    # （懒重算主路径；验证路径只做限量兜底）。仅启用状态下触发，避免关着开关
    # 还去打端点/NPU。任务进程内单例幂等，见 orchestrator.start_background_recompute。
    if payload.enabled:
        prior_mode = existing.mode if existing else None
        prior_endpoint = existing.endpoint if existing else None
        if (payload.mode, payload.endpoint) != (prior_mode, prior_endpoint):
            from face.orchestrator import start_background_recompute
            try:
                start_background_recompute(
                    tid, payload.mode, payload.endpoint, payload.auth_token)
            except Exception:
                # 后台补算失败绝不影响配置保存本身
                import logging
                logging.getLogger("warehouse.face").exception(
                    "start_background_recompute failed (non-fatal)")
    return {"success": True, "tenant_id": tid}


@router.get("/api/face/rules")
async def face_list_rules(
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    # Phase 2f: SA Core read.
    with get_engine().connect() as sa_conn:
        rows = sa_conn.execute(
            select(
                _t_tenant_face_rules.c.id, _t_tenant_face_rules.c.tenant_id,
                _t_tenant_face_rules.c.warehouse_id, _t_tenant_face_rules.c.operation,
                _t_tenant_face_rules.c.require_face, _t_tenant_face_rules.c.allowed_subject_ids,
                _t_tenant_face_rules.c.min_confidence_override,
            ).where(_t_tenant_face_rules.c.tenant_id == tid)
            .order_by(_t_tenant_face_rules.c.id.asc())
        ).fetchall()
    out = []
    for r in rows:
        raw = r.allowed_subject_ids
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode('utf-8')
        try:
            if isinstance(raw, str):
                allowed = _face_json.loads(raw) if raw else []
            else:
                allowed = raw if raw else []
        except Exception:
            allowed = []
        out.append({
            "id": r.id, "tenant_id": r.tenant_id, "warehouse_id": r.warehouse_id,
            "operation": r.operation, "require_face": bool(r.require_face),
            "allowed_subject_ids": allowed,
            "min_confidence_override": r.min_confidence_override,
        })
    return out


@router.post("/api/face/rules")
async def face_create_rule(
    payload: FaceRulePayload,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    allowed_value = payload.allowed_subject_ids if payload.allowed_subject_ids else None
    with get_engine().begin() as sa_conn:
        result = sa_conn.execute(
            insert(_t_tenant_face_rules).values(
                tenant_id=tid,
                warehouse_id=payload.warehouse_id,
                operation=payload.operation,
                require_face=1 if payload.require_face else 0,
                allowed_subject_ids=allowed_value,
                min_confidence_override=payload.min_confidence_override,
            )
        )
        rid = result.inserted_primary_key[0] if result.inserted_primary_key else None
    return {"id": rid}


@router.put("/api/face/rules/{rule_id}")
async def face_update_rule(
    rule_id: int,
    payload: FaceRulePayload,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    allowed_value = payload.allowed_subject_ids if payload.allowed_subject_ids else None
    with get_engine().begin() as sa_conn:
        load_or_404(
            sa_conn, _t_tenant_face_rules, rule_id,
            columns=[_t_tenant_face_rules.c.tenant_id],
            not_found="规则不存在",
            tenant_id=tid,
            forbidden="无权修改该规则",
        )
        sa_conn.execute(
            update(_t_tenant_face_rules)
            .where(_t_tenant_face_rules.c.id == rule_id)
            .values(
                warehouse_id=payload.warehouse_id,
                operation=payload.operation,
                require_face=1 if payload.require_face else 0,
                allowed_subject_ids=allowed_value,
                min_confidence_override=payload.min_confidence_override,
            )
        )
    return {"success": True, "id": rule_id}


@router.delete("/api/face/rules/{rule_id}")
async def face_delete_rule(
    rule_id: int,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    with get_engine().begin() as sa_conn:
        load_or_404(
            sa_conn, _t_tenant_face_rules, rule_id,
            columns=[_t_tenant_face_rules.c.tenant_id],
            not_found="规则不存在",
            tenant_id=tid,
            forbidden="无权删除该规则",
        )
        sa_conn.execute(
            delete(_t_tenant_face_rules).where(_t_tenant_face_rules.c.id == rule_id)
        )
    return {"success": True}


@router.get("/api/face/enrollments")
async def face_list_enrollments(
    subject_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    """List face enrollments. Big columns (embedding/source_image_b64) are stripped by default. — Phase 2f: SA Core read."""
    tid = _face_resolve_tenant(current_user, tenant_id)
    preds = [_t_face_enrollments.c.tenant_id == tid]
    if subject_id is not None:
        preds.append(_t_face_enrollments.c.subject_id == subject_id)
    stmt = (
        select(
            _t_face_enrollments.c.id, _t_face_enrollments.c.subject_id,
            _t_face_enrollments.c.tenant_id, _t_face_enrollments.c.model_tag,
            _t_face_enrollments.c.applies_to_warehouse_ids,
            _t_face_enrollments.c.is_active, _t_face_enrollments.c.enrolled_at,
            _t_face_enrollments.c.enrolled_by,
        )
        .where(and_(*preds))
        .order_by(_t_face_enrollments.c.id.desc())
    )
    with get_engine().connect() as sa_conn:
        rows = sa_conn.execute(stmt).fetchall()
    out = []
    for r in rows:
        raw = r.applies_to_warehouse_ids
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode('utf-8')
        try:
            if isinstance(raw, str):
                applies = _face_json.loads(raw) if raw else None
            else:
                applies = raw if raw else None
        except Exception:
            applies = None
        out.append({
            "id": r.id, "subject_id": r.subject_id, "tenant_id": r.tenant_id,
            "model_tag": r.model_tag, "applies_to_warehouse_ids": applies,
            "is_active": bool(r.is_active),
            "enrolled_at": r.enrolled_at, "enrolled_by": r.enrolled_by,
        })
    return out


def build_face_library(tid: int, model_tag: Optional[str] = None) -> List[dict]:
    """Build the per-tenant face library (active subjects × active enrollments).

    Returns ``[{name, subject_id, embedding_b64, model_tag}]``. When ``model_tag``
    is given, only enrollments produced by that embedding model are returned —
    embeddings live in different vector spaces per model, so mixing them across
    model_tags would corrupt on-device matching. Shared by the ``/api/face/library``
    export route and the MCP "push faces to device" flow.
    """
    preds = [
        _t_face_enrollments.c.tenant_id == tid,
        _t_face_enrollments.c.is_active == 1,
        _t_face_subjects.c.is_active == 1,
    ]
    if model_tag is not None:
        preds.append(_t_face_enrollments.c.model_tag == model_tag)
    stmt = (
        select(
            _t_face_subjects.c.name,
            _t_face_subjects.c.id.label("subject_id"),
            _t_face_enrollments.c.embedding,
            _t_face_enrollments.c.model_tag,
        )
        .select_from(
            _t_face_enrollments.join(
                _t_face_subjects,
                _t_face_subjects.c.id == _t_face_enrollments.c.subject_id,
            )
        )
        .where(and_(*preds))
    )
    with get_engine().connect() as sa_conn:
        rows = sa_conn.execute(stmt).fetchall()
    out = []
    for r in rows:
        if r.embedding is None:
            continue
        out.append({
            "name": r.name,
            "subject_id": r.subject_id,
            "embedding_b64": _face_base64.b64encode(r.embedding).decode(),
            "model_tag": r.model_tag,
        })
    return out


@router.get("/api/face/library")
async def face_library(
    tenant_id: Optional[int] = None,
    model_tag: Optional[str] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.WRITE)),
):
    """Export the face library for on-device sync.

    Returns ``[{name, subject_id, embedding_b64, model_tag}]`` — active subjects
    joined with their active enrollments, embedding bytes base64-encoded. xiaozhi
    pulls this and pushes each entry to the device-local DB via ``self.face.add``
    so passive greeting recognizes the same people the face check authorizes. The
    device persists ``subject_id`` alongside ``name`` and returns it via
    ``self.conversation.speaker`` so session-mode verify can locate the subject
    without name ambiguity. Same auth (FACE/WRITE) as verify-mcp, so the MCP
    api_key can call it.

    Optional ``model_tag`` filters to enrollments from a single embedding model;
    omit it to keep the legacy (unfiltered) behaviour.
    """
    tid = _face_resolve_tenant(current_user, tenant_id)
    return build_face_library(tid, model_tag=model_tag)


@router.post("/api/face/enrollments")
async def face_create_enrollment(
    payload: FaceEnrollmentPayload,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    if not payload.images_b64 and not payload.embeddings:
        raise HTTPException(status_code=400, detail="必须提供至少一张人脸图片或一个 embedding")
    if payload.images_b64 and payload.embeddings:
        raise HTTPException(status_code=400, detail="images_b64 与 embeddings 不能同时提供")
    with get_engine().connect() as sa_conn:
        load_or_404(
            sa_conn, _t_face_subjects, payload.subject_id,
            columns=[_t_face_subjects.c.tenant_id],
            not_found="人员档案不存在",
            tenant_id=int(tid),
            forbidden="人员档案不属于该租户",
        )
    # Decode b64 embeddings up front so we fail with a 400 (not 500) on
    # malformed input.
    precomputed: list[dict] = []
    for item in payload.embeddings:
        try:
            precomputed.append({
                "embedding_bytes": _face_base64.b64decode(item.embedding_b64),
                "model_tag": item.model_tag,
            })
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无效的 embedding_b64: {e}")
    # TODO: migrate to SA Core in a future pass — orchestrator currently
    # expects a sqlite-style ``conn`` for its internal writes.
    with get_db() as conn:
        # backend 走 `sys.path.insert(0, 'backend')` 平铺布局
        # （run_backend.py + tests/conftest.py 都这么做），face 子包以
        # 顶层名访问。无需双路径 try/except fallback。
        from face.orchestrator import enroll_face as _enroll
        from face.endpoint_client import FaceEndpointError
        try:
            result = await _enroll(
                conn,
                subject_id=payload.subject_id,
                tenant_id=tid,
                images_b64=payload.images_b64 or None,
                precomputed=precomputed or None,
                applies_to_warehouse_ids=payload.applies_to_warehouse_ids,
                enrolled_by=current_user.id,
            )
        except FaceEndpointError as e:
            raise HTTPException(status_code=502, detail=f"face endpoint error: {e}")
        return {"success": True, **result}


@router.delete("/api/face/enrollments/{enrollment_id}")
async def face_delete_enrollment(
    enrollment_id: int,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    with get_engine().begin() as sa_conn:
        load_or_404(
            sa_conn, _t_face_enrollments, enrollment_id,
            columns=[_t_face_enrollments.c.tenant_id],
            not_found="enrollment 不存在",
            tenant_id=tid,
            forbidden="无权删除该 enrollment",
        )
        sa_conn.execute(
            delete(_t_face_enrollments).where(_t_face_enrollments.c.id == enrollment_id)
        )
    return {"success": True}


@router.post("/api/face/test-connection")
async def face_test_connection(
    payload: FaceTestConnectionPayload,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    from face.endpoint_client import health as _health, FaceEndpointError
    try:
        info = await _health(payload.endpoint, payload.auth_token)
        return {"success": True, "info": info}
    except FaceEndpointError as e:
        return {"success": False, "error": str(e)}


@router.get("/api/face/logs")
async def face_list_logs(
    user_id: Optional[int] = None,
    operation: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 500:
        page_size = 50
    # Phase 2f: SA Core read.
    preds = [_t_face_auth_logs.c.tenant_id == tid]
    if user_id is not None:
        preds.append(_t_face_auth_logs.c.user_id == user_id)
    if operation:
        preds.append(_t_face_auth_logs.c.operation == operation)
    if start:
        preds.append(_t_face_auth_logs.c.created_at >= start)
    if end:
        preds.append(_t_face_auth_logs.c.created_at <= end)
    offset = (page - 1) * page_size
    with get_engine().connect() as sa_conn:
        total = sa_conn.execute(
            select(_sa_func.count()).select_from(_t_face_auth_logs).where(and_(*preds))
        ).scalar() or 0
        rows = sa_conn.execute(
            select(_t_face_auth_logs)
            .where(and_(*preds))
            .order_by(_t_face_auth_logs.c.created_at.desc(), _t_face_auth_logs.c.id.desc())
            .limit(page_size).offset(offset)
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r._mapping)
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = d['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ============ MCP-only Face Verify Bridge ============
# 用于 MCP wrapper 在调用 stock_in/stock_out 等写入工具前向后端确认身份。
# 此端点本身不修改库存，仅返回 Decision；HTTP 出入库端点完全不受影响。

class FaceVerifyMcpPayload(BaseModel):
    operation: str
    warehouse_id: Optional[int] = None
    request_id: Optional[str] = None
    # One of these two when the rule actually requires face (skipped paths
    # ignore both):
    #   image_b64   — server-side path; warehouse calls /infer.
    #   embedding_b64 + embedding_model_tag — device already inferred
    #                 (Himax WE2 NPU); warehouse just matches.
    image_b64: Optional[str] = None
    embedding_b64: Optional[str] = None
    embedding_model_tag: Optional[str] = None
    # Session-mode (advisory) identity. Design: the LLM calls the device tool
    # ``self.conversation.speaker`` (the on-board face-match result) and forwards
    # subject_id / name here as LLM-visible tool args — NOT server-injected. So a
    # spoken self-introduction is a distinct thing from the device's verified
    # speaker; the P1-2 probe (e2e_voice_mcp/test_face_speaker_injection.py)
    # confirms the official LLM does not populate these from a mere spoken claim.
    # Consulted only when verify_mode == 'session'; ignored under 'interface'
    # (which re-matches the embedding, fail-closed).
    speaker_subject_id: Optional[int] = None
    speaker_name: Optional[str] = None
    # 多设备消歧（一个 MCP 连接挂多台设备时）。由 MCP 传输层透传的可信设备标识，
    # 不接受 LLM 自填。单设备部署留空即可。session 模式（B）用它精确定位设备。
    device_id: Optional[str] = None


@router.post("/api/face/verify-mcp")
async def face_verify_mcp(
    payload: FaceVerifyMcpPayload,
    request: Request,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.WRITE)),
):
    # Observability (opt-in via FACE_VERIFY_MCP_TRACE=1): record the caller-supplied
    # identity params as they arrive — the observation point for the P1-2 probe
    # (e2e_voice_mcp/test_face_speaker_injection.py reads this from the backend log).
    # Off by default to keep prod stdout quiet. Use print(flush) not a logger:
    # alembic's startup fileConfig disables existing loggers
    # (disable_existing_loggers=True), so a module logger would be silenced.
    if _os.environ.get("FACE_VERIFY_MCP_TRACE"):
        print(
            f"[FACE-VERIFY-MCP] tenant={current_user.tenant_id} op={payload.operation} "
            f"speaker_subject_id={payload.speaker_subject_id!r} speaker_name={payload.speaker_name!r} "
            f"has_image={bool(payload.image_b64)} has_embedding={bool(payload.embedding_b64)}",
            flush=True,
        )
    # tenant_id must be concrete; global admin without tenant has no rules to evaluate
    if current_user.tenant_id is None:
        return {"status": "skipped", "failure_reason": "no_tenant_context",
                "confidence": None, "matched_subject_id": None}
    from face.orchestrator import verify_mcp_face as _verify
    from face.device_pull import resolve_pull_device
    warehouse_id = resolve_warehouse_id(current_user, payload.warehouse_id)
    emb_bytes: Optional[bytes] = None
    if payload.embedding_b64:
        try:
            emb_bytes = _face_base64.b64decode(payload.embedding_b64)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无效的 embedding_b64: {e}")
    # session 模式（B 方案）用请求头的明文 API Key 定位该连接绑定的设备，后端直连拉取
    # 身份。interface 模式不需要（走 embedding 重比对），resolve 返回 None 也无妨。
    pull_device = resolve_pull_device(
        request.headers.get("X-API-Key"),
        current_user.tenant_id,
        device_id=payload.device_id,
    )
    with get_db() as conn:
        decision = await _verify(
            conn,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            warehouse_id=warehouse_id,
            operation=payload.operation,
            image_b64=payload.image_b64 or "",
            embedding_bytes=emb_bytes,
            embedding_model_tag=payload.embedding_model_tag,
            speaker_subject_id=payload.speaker_subject_id,
            speaker_name=payload.speaker_name,
            pull_device=pull_device,
            request_id=payload.request_id,
        )
    return {
        "status": decision.status,
        "failure_reason": decision.failure_reason,
        "confidence": decision.confidence,
        "matched_subject_id": decision.matched_subject_id,
    }


# ============ Device Recognition Proxy (identify_mode='lan') ============
# 设备识别代理：lan 模式下设备现场抓拍后按 face_rec_api 的 /recognize 契约
# POST 到这里（push-faces 下发 identify_endpoint=http://<warehouse>:<port>/api/face/device，
# 固件拼 /recognize）。warehouse 转发到租户端点 /infer 取 embedding（含活体），
# 再对本库 face_enrollments 比对——人脸库只在 warehouse 存一份，face_rec_api
# 保持无状态推理。设备无 cookie 登录态，鉴权用租户级 tenant_face_config.auth_token
# （Bearer，push-faces 首发时自动生成）。


class DeviceRecognizePayload(BaseModel):
    image_base64: str


def _device_recognize_tenant(request: Request) -> int:
    """Bearer token 反查租户。token 为空 / 无租户命中 → 401。

    auth_token 为空的租户天然无法命中（谓词排除 NULL/''），不存在
    「空 token 匹配空列」的绕过。"""
    auth = request.headers.get("Authorization") or ""
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    with get_engine().connect() as sa_conn:
        row = sa_conn.execute(
            select(_t_tenant_face_config.c.tenant_id).where(and_(
                _t_tenant_face_config.c.auth_token == token,
                _t_tenant_face_config.c.auth_token.isnot(None),
                _t_tenant_face_config.c.auth_token != "",
            ))
        ).first()
    if row is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return int(row.tenant_id)


@router.post("/api/face/device/recognize")
async def face_device_recognize(payload: DeviceRecognizePayload, request: Request):
    """face_rec_api /recognize 契约的代理实现（设备直连，无登录态）。

    响应恒 200：{matched, name, confidence, processing_time_ms, live,
    liveness_score, reason}；spoof/no_face/端点错都以 matched=false + reason
    表达（契约如此，固件按 body 判定）。每次调用写一行 face_auth_logs
    （operation='device_recognize'，user_id=0 表示无系统用户的设备调用）。
    """
    import time as _time

    from face import endpoint_client as _ec
    from face.endpoint_client import FaceEndpointError
    from face.matcher import topk_match
    from face.orchestrator import (
        _ensure_model_enrollments,
        _load_config,
        _log_decision,
    )

    t0 = _time.monotonic()
    tid = _device_recognize_tenant(request)
    cfg = _load_config(None, tid)  # token 反查命中 → 该租户必有 config 行

    def _resp(matched: bool, *, name=None, confidence: float = 0.0,
              live=None, liveness_score=None, reason=None) -> dict:
        return {
            "matched": matched,
            "name": name,
            "confidence": round(float(confidence), 4),
            "processing_time_ms": int((_time.monotonic() - t0) * 1000),
            "live": live,
            "liveness_score": liveness_score,
            "reason": reason,
        }

    with get_db() as conn:
        def _audit(decision: str, *, subject_id=None, confidence=None, reason=None):
            _log_decision(
                conn, request_id=None, user_id=0, matched_subject_id=subject_id,
                tenant_id=tid, warehouse_id=None, operation="device_recognize",
                confidence=confidence, decision=decision, failure_reason=reason,
            )

        try:
            result = await _ec.infer(cfg, payload.image_base64)
        except FaceEndpointError as e:
            reason = str(e) or "endpoint_unreachable"
            _audit("deny", reason=reason)
            return _resp(
                False, reason=reason,
                # 契约：假体时 live=false；其余错误活体未知 → null。
                live=False if reason == "spoof" else None,
            )

        model_tag = result["model_tag"]
        # 懒重算兜底（与 verify 路径同一 helper）：换模型后老 subject 缺当前
        # model_tag 的 embedding 时用注册照片限量补算。异常不阻塞识别主链路。
        try:
            await _ensure_model_enrollments(conn, cfg, tid, model_tag)
        except Exception:
            import logging
            logging.getLogger("warehouse.face").exception(
                "device-recognize lazy re-embed failed (non-fatal)")

        matches = topk_match(
            conn, tenant_id=tid, warehouse_id=None, model_tag=model_tag,
            query_emb_bytes=result["embedding"], k=1,
        )
        best = matches[0] if matches else None
        threshold = cfg.min_confidence if cfg else 0.65
        if best is None or best.confidence < threshold:
            reason = "no_match" if best is None else "low_confidence"
            _audit(
                "deny", subject_id=best.subject_id if best else None,
                confidence=best.confidence if best else None, reason=reason,
            )
            return _resp(
                False, confidence=best.confidence if best else 0.0,
                reason="no_match",  # 契约不区分低分/无候选，统一 no_match
            )

        with get_engine().connect() as sa_conn:
            name = sa_conn.execute(
                select(_t_face_subjects.c.name)
                .where(_t_face_subjects.c.id == best.subject_id)
            ).scalar()
        _audit("pass", subject_id=best.subject_id, confidence=best.confidence)
        return _resp(True, name=name, confidence=best.confidence)


# ============ Face Subjects CRUD ============

@router.get("/api/face/subjects")
async def face_list_subjects(
    tenant_id: Optional[int] = None,
    include_inactive: bool = False,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    # Phase 2f: SA Core read.
    enroll_count = (
        select(_sa_func.count())
        .select_from(_t_face_enrollments)
        .where(and_(
            _t_face_enrollments.c.subject_id == _t_face_subjects.c.id,
            _t_face_enrollments.c.is_active == 1,
        ))
        .correlate(_t_face_subjects)
        .scalar_subquery()
        .label('enrollment_count')
    )
    preds = [_t_face_subjects.c.tenant_id == tid]
    if not include_inactive:
        preds.append(_t_face_subjects.c.is_active == 1)
    stmt = (
        select(
            _t_face_subjects.c.id, _t_face_subjects.c.tenant_id,
            _t_face_subjects.c.name, _t_face_subjects.c.employee_id,
            _t_face_subjects.c.note, _t_face_subjects.c.is_active,
            _t_face_subjects.c.created_by, _t_face_subjects.c.created_at,
            _t_face_subjects.c.updated_at, enroll_count,
        )
        .where(and_(*preds))
        .order_by(_t_face_subjects.c.id.asc())
    )
    with get_engine().connect() as sa_conn:
        rows = sa_conn.execute(stmt).fetchall()
    out = []
    for r in rows:
        d = dict(r._mapping)
        if isinstance(d.get('created_at'), datetime):
            d['created_at'] = d['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(d.get('updated_at'), datetime):
            d['updated_at'] = d['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
        out.append(d)
    return out


@router.post("/api/face/subjects")
async def face_create_subject(
    payload: FaceSubjectPayload,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="姓名不能为空")
    with get_engine().begin() as sa_conn:
        result = sa_conn.execute(
            insert(_t_face_subjects).values(
                tenant_id=tid,
                name=payload.name.strip(),
                employee_id=(payload.employee_id or None),
                note=(payload.note or None),
                is_active=1 if payload.is_active else 0,
                created_by=current_user.id,
                created_at=_sa_func.current_timestamp(),
                updated_at=_sa_func.current_timestamp(),
            )
        )
        sid = result.inserted_primary_key[0]
        return {"id": sid, "success": True}


@router.put("/api/face/subjects/{subject_id}")
async def face_update_subject(
    subject_id: int,
    payload: FaceSubjectPayload,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="姓名不能为空")
    with get_engine().begin() as sa_conn:
        load_or_404(
            sa_conn, _t_face_subjects, subject_id,
            columns=[_t_face_subjects.c.tenant_id],
            not_found="人员档案不存在",
            tenant_id=int(tid),
            forbidden="无权修改该档案",
        )
        sa_conn.execute(
            update(_t_face_subjects).where(_t_face_subjects.c.id == subject_id).values(
                name=payload.name.strip(),
                employee_id=(payload.employee_id or None),
                note=(payload.note or None),
                is_active=1 if payload.is_active else 0,
                updated_at=_sa_func.current_timestamp(),
            )
        )
        return {"success": True, "id": subject_id}


@router.delete("/api/face/subjects/{subject_id}")
async def face_delete_subject(
    subject_id: int,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    with get_engine().begin() as sa_conn:
        load_or_404(
            sa_conn, _t_face_subjects, subject_id,
            columns=[_t_face_subjects.c.tenant_id],
            not_found="人员档案不存在",
            tenant_id=int(tid),
            forbidden="无权删除该档案",
        )
        # ON DELETE CASCADE drops the enrollments; rules referencing this
        # subject will be silently dropped from allowed lists by stale-id
        # tolerance in the matcher (no explicit cleanup needed).
        sa_conn.execute(
            delete(_t_face_subjects).where(_t_face_subjects.c.id == subject_id)
        )
        return {"success": True}
