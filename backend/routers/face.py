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

from fastapi import APIRouter, Depends, HTTPException
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

class FaceRulePayload(BaseModel):
    warehouse_id: Optional[int] = None
    operation: str
    require_face: bool = False
    allowed_subject_ids: Optional[List[int]] = None
    min_confidence_override: Optional[float] = None

class FaceEnrollmentPayload(BaseModel):
    subject_id: int
    images_b64: List[str] = Field(default_factory=list)
    applies_to_warehouse_ids: Optional[List[int]] = None

class FaceSubjectPayload(BaseModel):
    name: str
    employee_id: Optional[str] = None
    note: Optional[str] = None
    is_active: bool = True

class FaceTestConnectionPayload(BaseModel):
    endpoint: str
    auth_token: Optional[str] = None


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
                _t_tenant_face_config.c.min_confidence, _t_tenant_face_config.c.created_at,
                _t_tenant_face_config.c.updated_at,
            ).where(_t_tenant_face_config.c.tenant_id == tid)
        ).first()
    if not row:
        return {"tenant_id": tid, "enabled": False, "mode": None, "endpoint": None,
                "auth_token": None, "embedding_model_tag": None, "min_confidence": 0.65}
    return {
        "tenant_id": row.tenant_id,
        "enabled": bool(row.enabled),
        "mode": row.mode,
        "endpoint": row.endpoint,
        "auth_token": row.auth_token,
        "embedding_model_tag": row.embedding_model_tag,
        "min_confidence": row.min_confidence,
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
    if payload.mode is not None and payload.mode not in ("local", "hello", "jetson", "custom"):
        raise HTTPException(status_code=400, detail="mode 必须是 local/hello/jetson/custom")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_engine().begin() as sa_conn:
        existing = sa_conn.execute(
            select(_t_tenant_face_config.c.id).where(_t_tenant_face_config.c.tenant_id == tid)
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
                    created_at=now,
                    updated_at=now,
                )
            )
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


@router.post("/api/face/enrollments")
async def face_create_enrollment(
    payload: FaceEnrollmentPayload,
    tenant_id: Optional[int] = None,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.ADMIN)),
):
    tid = _face_resolve_tenant(current_user, tenant_id)
    if not payload.images_b64:
        raise HTTPException(status_code=400, detail="必须提供至少一张人脸图片")
    with get_engine().connect() as sa_conn:
        load_or_404(
            sa_conn, _t_face_subjects, payload.subject_id,
            columns=[_t_face_subjects.c.tenant_id],
            not_found="人员档案不存在",
            tenant_id=int(tid),
            forbidden="人员档案不属于该租户",
        )
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
                images_b64=payload.images_b64,
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


@router.post("/api/face/verify-mcp")
async def face_verify_mcp(
    payload: FaceVerifyMcpPayload,
    current_user: 'CurrentUser' = Depends(require_permission(Resource.FACE, Action.WRITE)),
):
    # tenant_id must be concrete; global admin without tenant has no rules to evaluate
    if current_user.tenant_id is None:
        return {"status": "skipped", "failure_reason": "no_tenant_context",
                "confidence": None, "matched_subject_id": None}
    from face.orchestrator import verify_mcp_face as _verify
    with get_db() as conn:
        decision = await _verify(
            conn,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            warehouse_id=payload.warehouse_id,
            operation=payload.operation,
            request_id=payload.request_id,
        )
    return {
        "status": decision.status,
        "failure_reason": decision.failure_reason,
        "confidence": decision.confidence,
        "matched_subject_id": decision.matched_subject_id,
    }


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
