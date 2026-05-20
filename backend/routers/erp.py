"""ERP provider admin routes (extracted from app.py — Phase 2, task #6).

All routes keep their full literal ``/api/erp/...`` path (the router is
mounted without a prefix) so that the snapshot in
``tests/fixtures/route_inventory.json`` stays byte-for-byte identical.

The router depends only on shared primitives from ``deps.py``, ``db``,
``metadata``, ``resource_router`` and ``providers.*``; nothing here imports
from ``app.py`` so we avoid a circular-import on FastAPI app boot.

Follow bare-module import style (no ``from backend.X``).
"""
import os
import sys as _sys
import json as _json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from db import get_engine
from deps import (
    Action,
    CurrentUser,
    Resource,
    assert_row_in_scope,
    build_scope_predicates,
    load_or_404,
    logger,
    require_permission,
)
from metadata import (
    erp_providers as _t_erp_providers,
    system_settings as _t_system_settings,
)
from resource_router import ResourceRouter

router = APIRouter()


# ============ ERP Provider 管理 APIs ============

# 将 mcp/providers 目录加入 sys.path，供动态加载 Provider 使用
# This module lives at backend/routers/erp.py, so three dirname() calls walk
# up to the project root (one extra level vs. the original app.py at
# backend/app.py). The resolved ``_mcp_dir`` value must remain identical.
_mcp_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'mcp',
)
if _mcp_dir not in _sys.path:
    _sys.path.insert(0, _mcp_dir)


def _get_providers_custom_dir(tenant_id: Optional[int] = None) -> str:
    """返回自定义 Provider 存储目录（确保存在）。按 tenant_id 隔离子目录。"""
    base = os.path.join(_mcp_dir, 'providers', 'custom')
    if tenant_id is not None:
        custom_dir = os.path.join(base, str(tenant_id))
    else:
        custom_dir = base
    os.makedirs(custom_dir, exist_ok=True)
    return custom_dir


@router.get("/api/erp/providers")
async def list_erp_providers(current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN))):
    """列出所有 ERP Provider — Phase 2f: SA Core read."""
    preds = list(build_scope_predicates(_t_erp_providers, current_user.tenant_id, None))
    stmt = select(
        _t_erp_providers.c.id, _t_erp_providers.c.name,
        _t_erp_providers.c.provider_name, _t_erp_providers.c.class_name,
        _t_erp_providers.c.filename, _t_erp_providers.c.config,
        _t_erp_providers.c.test_results, _t_erp_providers.c.test_passed_at,
        _t_erp_providers.c.is_active, _t_erp_providers.c.created_at,
        _t_erp_providers.c.updated_at,
    )
    if preds:
        stmt = stmt.where(and_(*preds))
    stmt = stmt.order_by(_t_erp_providers.c.created_at.desc())
    with get_engine().connect() as sa_conn:
        rows = sa_conn.execute(stmt).fetchall()

    providers = []
    for row in rows:
        p = dict(row._mapping)
        cfg = p.get('config')
        if isinstance(cfg, (bytes, bytearray)):
            cfg = cfg.decode('utf-8')
        if isinstance(cfg, str):
            p['config'] = _json.loads(cfg) if cfg else {}
        else:
            p['config'] = cfg if cfg else {}
        tr = p.get('test_results')
        if isinstance(tr, (bytes, bytearray)):
            tr = tr.decode('utf-8')
        if isinstance(tr, str):
            p['test_results'] = _json.loads(tr) if tr else None
        else:
            p['test_results'] = tr if tr else None
        if isinstance(p.get('created_at'), datetime):
            p['created_at'] = p['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(p.get('updated_at'), datetime):
            p['updated_at'] = p['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(p.get('test_passed_at'), datetime):
            p['test_passed_at'] = p['test_passed_at'].strftime('%Y-%m-%d %H:%M:%S')
        providers.append(p)
    return {"providers": providers}


@router.post("/api/erp/providers")
async def upload_erp_provider(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN))
):
    """上传自定义 Provider .py 文件"""
    import tempfile
    import shutil

    # 校验扩展名
    if not file.filename or not file.filename.endswith('.py'):
        raise HTTPException(status_code=400, detail="只接受 .py 文件")

    # 读取内容并检查大小
    content = await file.read()
    if len(content) > 100 * 1024:
        raise HTTPException(status_code=400, detail="文件大小超过 100KB 上限")

    # 写入临时文件后校验
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.py')
    try:
        with os.fdopen(tmp_fd, 'wb') as f:
            f.write(content)

        from providers.validator import validate_provider_file
        result = validate_provider_file(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not result['valid']:
        raise HTTPException(status_code=400, detail={
            "message": "Provider 文件校验失败",
            "errors": result['errors'],
        })

    provider_name = result['provider_name']
    class_name = result['class_name']
    filename = f"{provider_name}.py"

    # 全局管理员必须显式指定 tenant
    target_tid = current_user.tenant_id
    if target_tid is None:
        raise HTTPException(status_code=400, detail="全局管理员上传 ERP Provider 时需指定 tenant_id")

    # 保存到 custom 目录（按 tenant_id 隔离）
    custom_dir = _get_providers_custom_dir(tenant_id=target_tid)
    dest_path = os.path.join(custom_dir, filename)
    with open(dest_path, 'wb') as f:
        f.write(content)

    # 写入数据库
    now_dt = datetime.now()
    # 使用文件名（去掉.py）作为默认显示名
    display_name = file.filename.replace('.py', '')
    try:
        with get_engine().begin() as sa_conn:
            result = sa_conn.execute(
                insert(_t_erp_providers).values(
                    name=display_name,
                    provider_name=provider_name,
                    class_name=class_name,
                    filename=filename,
                    tenant_id=target_tid,
                    created_at=now_dt,
                    updated_at=now_dt,
                )
            )
            provider_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
    except IntegrityError:
        # provider_name 在当前租户内唯一约束冲突
        os.unlink(dest_path)
        raise HTTPException(status_code=409, detail=f"Provider '{provider_name}' 在当前租户下已存在")

    logger.info(f"上传 ERP Provider: {provider_name} ({class_name})，操作人: {current_user.display_name}")
    return {
        "id": provider_id,
        "provider_name": provider_name,
        "class_name": class_name,
        "filename": filename,
        "methods": result['methods'],
    }


def _ensure_provider_tenant(row, current_user: CurrentUser):
    """确认 provider 属于当前租户（全局 admin 例外）。失败抛 403。"""
    assert_row_in_scope(
        row, current_user, forbidden="无权操作其他租户的 Provider"
    )


@router.get("/api/erp/providers/active-for-mcp")
async def get_active_provider_for_mcp(
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.READ))
):
    """返回当前租户激活的 ERP Provider 信息，供 MCP 引导使用。

    多租户隔离：使用 build_scope_filter 按 current_user.tenant_id 过滤，
    防止 MCP 通过裸 sqlite 拿到其他租户的 Provider（旧代码的跨租户泄露点）。

    返回：
        - 系统模式非 external_erp：{"mode": "self_owned", "provider": null}
        - external_erp 但当前租户没有激活的 Provider：404
        - 否则：{"mode": "external_erp", "provider": {id, provider_name, filename, config}}
    """
    # Phase 2f: SA Core read.
    with get_engine().connect() as sa_conn:
        m = sa_conn.execute(
            select(_t_system_settings.c.value).where(_t_system_settings.c.key == 'system_mode')
        ).first()
        mode = m.value if m else 'self_owned'

        if mode != 'external_erp':
            return {"mode": "self_owned", "provider": None}

        preds = [_t_erp_providers.c.is_active == 1]
        preds.extend(build_scope_predicates(_t_erp_providers, current_user.tenant_id, None))
        provider_row = sa_conn.execute(
            select(
                _t_erp_providers.c.id, _t_erp_providers.c.provider_name,
                _t_erp_providers.c.filename, _t_erp_providers.c.config,
            )
            .where(and_(*preds))
            .order_by(_t_erp_providers.c.id.asc())
            .limit(1)
        ).first()

    if not provider_row:
        raise HTTPException(
            status_code=404,
            detail="当前租户没有激活的 ERP Provider"
        )

    cfg = provider_row.config
    if isinstance(cfg, (bytes, bytearray)):
        cfg = cfg.decode('utf-8')
    if isinstance(cfg, str):
        cfg_obj = _json.loads(cfg) if cfg else {}
    else:
        cfg_obj = cfg if cfg else {}
    return {
        "mode": "external_erp",
        "provider": {
            "id": provider_row.id,
            "provider_name": provider_row.provider_name,
            "filename": provider_row.filename,
            "config": cfg_obj,
        },
    }


# ---- ERP Providers GET / PUT / DELETE migrated to ResourceRouter (R2 phase 3) ----
# LIST stays as ``list_erp_providers`` (custom shape ``{"providers": [...]}``
# with per-row JSON/datetime decoding) and POST stays hand-rolled (multipart
# UploadFile). Side-routes /test, /activate, /deactivate, /status remain.


class _UpdateERPProviderRequest(BaseModel):
    """PUT /api/erp/providers/{id} body. Original handler reads raw
    ``request.json()`` — to preserve wire shape we accept both keys
    individually and treat missing/null with the original semantics
    (``body.get('config', {})`` -> default empty dict; explicit ``None`` -> no
    update).
    """
    name: Optional[str] = None
    # ``Any`` because the existing PUT writes whatever JSON shape the client
    # sends back into the column; restricting to ``Dict[str, Any]`` would be
    # a forbidden wire-shape narrowing.
    config: Any = Field(default_factory=dict)


def _erp_decode_config(p: dict) -> Any:
    cfg = p.get('config')
    if isinstance(cfg, (bytes, bytearray)):
        cfg = cfg.decode('utf-8')
    if isinstance(cfg, str):
        return _json.loads(cfg) if cfg else {}
    return cfg if cfg else {}


def _erp_decode_test_results(p: dict) -> Any:
    tr = p.get('test_results')
    if isinstance(tr, (bytes, bytearray)):
        tr = tr.decode('utf-8')
    if isinstance(tr, str):
        return _json.loads(tr) if tr else None
    return tr if tr else None


def _erp_to_out(row) -> dict:
    p = dict(row._mapping)
    p['config'] = _erp_decode_config(p)
    p['test_results'] = _erp_decode_test_results(p)
    if isinstance(p.get('created_at'), datetime):
        p['created_at'] = p['created_at'].strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(p.get('updated_at'), datetime):
        p['updated_at'] = p['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(p.get('test_passed_at'), datetime):
        p['test_passed_at'] = p['test_passed_at'].strftime('%Y-%m-%d %H:%M:%S')
    return p


def _erp_values_for_update(sa_conn, current_user, request: _UpdateERPProviderRequest, row) -> dict:
    values: dict = {'updated_at': datetime.now()}
    if request.name is not None:
        values['name'] = request.name
    # Preserve original ``if config is not None`` semantics — explicit null
    # leaves the column untouched; missing -> default {} writes empty dict.
    if request.config is not None:
        values['config'] = request.config
    return values


def _erp_before_delete(sa_conn, current_user, row):
    # ``row`` here is loaded with [id, tenant_id, is_active, filename,
    # provider_name] (see ``load_columns`` below).
    if row.is_active:
        raise HTTPException(status_code=400, detail="请先停用 Provider 再删除")
    # File unlink (best-effort) before SQL delete — same order as the
    # original hand-rolled handler.
    custom_dir = _get_providers_custom_dir(tenant_id=row.tenant_id)
    filepath = os.path.join(custom_dir, row.filename)
    if os.path.exists(filepath):
        os.unlink(filepath)
    # Hard-delete the DB row.
    sa_conn.execute(delete(_t_erp_providers).where(_t_erp_providers.c.id == row.id))
    logger.info(f"删除 ERP Provider: {row.provider_name}，操作人: {current_user.display_name}")


def _erp_to_out_update(row, *, request, item_id, sa_conn, current_user) -> dict:
    return {"success": True}


def _erp_values_for_create_unused(sa_conn, current_user, request) -> dict:  # noqa
    # POST is hand-rolled (multipart). Hook left as required-signature stub.
    return {}


_erp_router = ResourceRouter(
    app=router,
    prefix="/api/erp/providers",
    table=_t_erp_providers,
    response_model=None,  # GET returns dict-of-Any (config/test_results)
    create_model=_UpdateERPProviderRequest,  # placeholder — POST disabled
    update_model=_UpdateERPProviderRequest,
    permission_read=require_permission(Resource.ERP, Action.ADMIN),
    permission_write=require_permission(Resource.ERP, Action.ADMIN),
    not_found_detail="Provider 不存在",
    forbidden_detail="无权操作其他租户的 Provider",
    to_out=_erp_to_out,
    values_for_create=_erp_values_for_create_unused,
    values_for_update=_erp_values_for_update,
    to_out_update=_erp_to_out_update,
    before_delete=_erp_before_delete,
    list_handler=None,
    enable_post=False,  # multipart upload — hand-rolled
    # DELETE wire shape: ``{"success": True}`` (no message). Default already
    # matches.
    # Load extra columns for the DELETE precondition (is_active / filename /
    # provider_name) so before_delete can read them atomically with the scope
    # check rather than re-querying.
    load_columns=[
        _t_erp_providers.c.id, _t_erp_providers.c.tenant_id,
        _t_erp_providers.c.is_active, _t_erp_providers.c.filename,
        _t_erp_providers.c.provider_name,
    ],
)
_erp_router.register()


@router.post("/api/erp/providers/{provider_id}/test")
async def test_erp_provider(
    provider_id: int,
    level: int = Query(1, ge=1, le=2),
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN))
):
    """运行 Provider 连通性测试（level=1 只读，level=2 写操作）"""
    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_erp_providers).where(_t_erp_providers.c.id == provider_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    if not row:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    _ensure_provider_tenant(row, current_user)

    _cfg = row['config']
    if isinstance(_cfg, (bytes, bytearray)):
        _cfg = _cfg.decode('utf-8')
    config = (_json.loads(_cfg) if _cfg else {}) if isinstance(_cfg, str) else (_cfg or {})
    custom_dir = _get_providers_custom_dir(tenant_id=row.get('tenant_id'))
    filepath = os.path.join(custom_dir, row['filename'])

    if not os.path.exists(filepath):
        raise HTTPException(status_code=400, detail=f"Provider 文件不存在: {row['filename']}")

    from providers.test_runner import run_level1_tests, run_level2_tests
    if level == 1:
        test_result = run_level1_tests(filepath, config)
    else:
        test_result = run_level2_tests(filepath, config)

    # 存储测试结果（分级保存，L1 和 L2 独立存储）
    now_dt = datetime.now()
    with get_engine().begin() as sa_conn:
        # 读取现有测试结果
        existing = sa_conn.execute(
            select(_t_erp_providers.c.test_results).where(_t_erp_providers.c.id == provider_id)
        ).first()
        existing_tr = existing.test_results if existing else None
        if isinstance(existing_tr, (bytes, bytearray)):
            existing_tr = existing_tr.decode('utf-8')
        if isinstance(existing_tr, str):
            all_results = _json.loads(existing_tr) if existing_tr else {}
        else:
            all_results = existing_tr if existing_tr else {}
        all_results[f'level{level}'] = test_result

        # L1 通过才更新 test_passed_at
        l1 = all_results.get('level1', {})
        test_passed_at = now_dt if l1.get('all_passed') else None

        sa_conn.execute(
            update(_t_erp_providers)
            .where(_t_erp_providers.c.id == provider_id)
            .values(test_results=all_results, test_passed_at=test_passed_at, updated_at=now_dt)
        )

    logger.info(f"测试 ERP Provider: {row['provider_name']} L{level}，all_passed={test_result['all_passed']}")
    return test_result


@router.post("/api/erp/providers/{provider_id}/activate")
async def activate_erp_provider(
    provider_id: int,
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN))
):
    """激活指定 Provider（需先通过 Level 1 测试）"""
    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_erp_providers).where(_t_erp_providers.c.id == provider_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    if not row:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    _ensure_provider_tenant(row, current_user)

    # 校验 Level 1 测试通过
    _tr = row['test_results']
    if isinstance(_tr, (bytes, bytearray)):
        _tr = _tr.decode('utf-8')
    test_results = (_json.loads(_tr) if _tr else None) if isinstance(_tr, str) else _tr
    l1 = test_results.get('level1', {}) if test_results else {}
    if not l1.get('all_passed'):
        raise HTTPException(status_code=400, detail="请先通过 Level 1 测试再激活")

    now_dt = datetime.now()
    # 仅停用同租户的其他 Provider —— 不能误伤其他租户的激活记录
    target_tenant_id = row['tenant_id']
    with get_engine().begin() as sa_conn:
        if target_tenant_id is None:
            sa_conn.execute(
                update(_t_erp_providers)
                .where(_t_erp_providers.c.tenant_id.is_(None))
                .values(is_active=0, updated_at=now_dt)
            )
        else:
            sa_conn.execute(
                update(_t_erp_providers)
                .where(_t_erp_providers.c.tenant_id == target_tenant_id)
                .values(is_active=0, updated_at=now_dt)
            )
        sa_conn.execute(
            update(_t_erp_providers)
            .where(_t_erp_providers.c.id == provider_id)
            .values(is_active=1, updated_at=now_dt)
        )

    logger.info(f"激活 ERP Provider: {row['provider_name']}，操作人: {current_user.display_name}")
    return {"success": True, "provider_name": row['provider_name']}


@router.post("/api/erp/providers/{provider_id}/deactivate")
async def deactivate_erp_provider(
    provider_id: int,
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN))
):
    """停用指定 Provider"""
    with get_engine().connect() as sa_conn:
        row = load_or_404(
            sa_conn, _t_erp_providers, provider_id,
            columns=[
                _t_erp_providers.c.id,
                _t_erp_providers.c.provider_name,
                _t_erp_providers.c.tenant_id,
            ],
            not_found="Provider 不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权操作其他租户的 Provider",
        )

    now_dt = datetime.now()
    with get_engine().begin() as sa_conn:
        sa_conn.execute(
            update(_t_erp_providers)
            .where(_t_erp_providers.c.id == provider_id)
            .values(is_active=0, updated_at=now_dt)
        )

    logger.info(f"停用 ERP Provider: {row.provider_name}，操作人: {current_user.display_name}")
    return {"success": True}


@router.get("/api/erp/providers/{provider_id}/status")
async def get_erp_provider_status(
    provider_id: int,
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN))
):
    """实时检测 Provider 连通性（调用 get_today_statistics 作为健康探针）"""
    import time as _time

    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_erp_providers).where(_t_erp_providers.c.id == provider_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    if not row:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    _ensure_provider_tenant(row, current_user)

    _cfg = row['config']
    if isinstance(_cfg, (bytes, bytearray)):
        _cfg = _cfg.decode('utf-8')
    config = (_json.loads(_cfg) if _cfg else {}) if isinstance(_cfg, str) else (_cfg or {})
    custom_dir = _get_providers_custom_dir(tenant_id=row.get('tenant_id'))
    filepath = os.path.join(custom_dir, row['filename'])

    if not os.path.exists(filepath):
        return {"online": False, "latency_ms": None, "error": f"Provider 文件不存在: {row['filename']}"}

    try:
        from providers.test_runner import load_provider_from_file
        t0 = _time.perf_counter()
        provider = load_provider_from_file(filepath, config)
        provider.get_today_statistics()
        latency_ms = round((_time.perf_counter() - t0) * 1000, 2)
        return {"online": True, "latency_ms": latency_ms, "error": None}
    except Exception as e:
        return {"online": False, "latency_ms": None, "error": f"{type(e).__name__}: {e}"}
