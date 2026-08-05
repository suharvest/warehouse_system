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
    users as _t_users,
    warehouses as _t_warehouses,
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
            # MCP 侧据此定位 custom/<tenant_id>/<filename>（上传时的实际布局）
            "tenant_id": current_user.tenant_id,
            "config": cfg_obj,
        },
    }


# ============ 外部作用域探测（external_erp 模式下配置智能体用） ============
# 接了外部 WMS 之后，库存数据全在对方，我们的租户/仓库跟对方的没有任何对应关系。
# 与其在本地镜像一套对方的组织结构（双重维护、必然漂移），不如让 Provider 把
# "对方有什么"报上来，用户在配置智能体时直接选，我们只存原始编码并原样透传。
#
# 探测哪个租户的？不需要推导——用调用方登录态的 tenant_id，与 active-for-mcp
# 同一套解析；全局 admin（tenant_id 为 None）必须显式传 tenant_id，与上传接口
# 的既有约定一致。


def _resolve_probe_tenant(current_user: CurrentUser, tenant_id: Optional[int]) -> int:
    """确定要探测哪个租户的外部系统。"""
    if current_user.tenant_id is not None:
        return current_user.tenant_id
    if tenant_id is None:
        raise HTTPException(
            status_code=400, detail="全局管理员探测外部作用域时需指定 tenant_id"
        )
    return tenant_id


def _load_active_provider_instance(tid: int):
    """按租户加载其激活的 Provider 实例，用于只读探测。

    与 mcp/warehouse_mcp.py 的加载逻辑保持一致：文件按
    「租户子目录 → 扁平路径」顺序解析，配置取 erp_providers.config
    （其中含对方 WMS 的地址与鉴权）。
    """
    with get_engine().connect() as sa_conn:
        preds = [_t_erp_providers.c.is_active == 1]
        preds.extend(build_scope_predicates(_t_erp_providers, tid, None))
        row = sa_conn.execute(
            select(
                _t_erp_providers.c.provider_name,
                _t_erp_providers.c.filename,
                _t_erp_providers.c.config,
            ).where(and_(*preds)).order_by(_t_erp_providers.c.id.asc()).limit(1)
        ).first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="当前租户没有激活的 ERP Provider，无法探测外部租户/仓库",
        )

    cfg = _erp_decode_config({"config": row.config}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg = {**cfg, "provider": row.provider_name}

    base = os.path.join(_mcp_dir, "providers", "custom")
    candidates = [
        os.path.join(base, str(tid), row.filename),
        os.path.join(base, row.filename),
    ]
    filepath = next((p for p in candidates if os.path.exists(p)), None)
    if filepath is None:
        raise HTTPException(
            status_code=404,
            detail=f"Provider 文件不存在（已尝试: {candidates}）",
        )

    try:
        from providers.test_runner import load_provider_from_file
        return load_provider_from_file(filepath, cfg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载 Provider 失败: {e}")


# 第三方 Provider 里的网络调用最多 ~10s（BaseProvider 的 timeout），但裸死循环或
# 自定义 http 客户端不受此约束。探测跑在 async 路由里，同步执行会直接堵住事件循环、
# 拖垮整个 worker。故放线程池并设硬超时。
_PROBE_TIMEOUT_SECONDS = 20.0


async def _probe(fn, *args) -> dict:
    """执行一次探测调用，把 Provider 抛出的异常收敛成结构化响应。

    恒返回 200：前端据 success / error 决定是渲染下拉还是退化成手工填写，
    未实现探测（not_implemented）属于预期路径，不该表现为 HTTP 错误。
    """
    import asyncio
    import functools

    try:
        resp = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None, functools.partial(fn, *args)
            ),
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("外部作用域探测超时（>%ss）", _PROBE_TIMEOUT_SECONDS)
        return {
            "success": False,
            "error": "probe_timeout",
            "items": [],
            "message": f"探测超时（超过 {int(_PROBE_TIMEOUT_SECONDS)} 秒），请检查外部系统响应速度",
        }
    except Exception as e:  # noqa: BLE001 — 第三方 Provider 代码，什么都可能抛
        logger.warning(f"外部作用域探测失败: {e}")
        return {
            "success": False,
            "error": "probe_failed",
            "items": [],
            "message": f"探测失败: {e}",
        }
    if not isinstance(resp, dict):
        return {
            "success": False,
            "error": "bad_response",
            "items": [],
            "message": "Provider 返回了非预期的响应结构",
        }
    resp.setdefault("items", [])
    return resp


@router.get("/api/erp/external/tenants")
async def probe_external_tenants(
    tenant_id: Optional[int] = None,
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN)),
):
    """探测外部系统的租户/组织列表（供智能体配置的下拉使用）。"""
    tid = _resolve_probe_tenant(current_user, tenant_id)
    provider = _load_active_provider_instance(tid)
    return await _probe(provider.list_tenants)


@router.get("/api/erp/external/warehouses")
async def probe_external_warehouses(
    external_tenant_id: Optional[str] = None,
    tenant_id: Optional[int] = None,
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN)),
):
    """探测外部系统的仓库列表；external_tenant_id 为已选定的外部租户。"""
    tid = _resolve_probe_tenant(current_user, tenant_id)
    provider = _load_active_provider_instance(tid)
    return await _probe(provider.list_warehouses, external_tenant_id)


@router.get("/api/erp/external/users")
async def probe_external_users(
    external_tenant_id: Optional[str] = None,
    tenant_id: Optional[int] = None,
    current_user: CurrentUser = Depends(require_permission(Resource.ERP, Action.ADMIN)),
):
    """探测外部系统的用户/账号列表（供导入使用）。

    要 ADMIN：这是拿来建我方登录账号的，比只读的租户/仓库探测敏感。
    """
    tid = _resolve_probe_tenant(current_user, tenant_id)
    provider = _load_active_provider_instance(tid)
    return await _probe(provider.list_users, external_tenant_id)


# ============ 外部身份导入 ============
# 授权是我方的责任，推不出去：谁能登录、谁能配哪个智能体、谁能改人脸规则，
# 走的是 users(role, tenant_id) + user_warehouses 这条链。所以即便库存数据
# 全在对方，「用户 → 租户/角色」这份归属数据仍必须落在我方。导入只是免去
# 管理员照着对方的用户表手工重敲一遍，不改变授权由我方判定这一事实。


class _ImportUserItem(BaseModel):
    external_user_id: str
    username: str
    display_name: Optional[str] = None
    role: str = "operate"


class _ImportUsersRequest(BaseModel):
    users: list[_ImportUserItem]
    # 导入只建身份，密码本地管：统一初始密码，导入后应要求用户自行修改。
    default_password: str
    tenant_id: Optional[int] = None


class _ImportWarehouseItem(BaseModel):
    external_warehouse_id: str
    name: str


class _ImportWarehousesRequest(BaseModel):
    warehouses: list[_ImportWarehouseItem]
    tenant_id: Optional[int] = None


def _resolve_import_tenant(current_user: CurrentUser, tenant_id: Optional[int]) -> int:
    return _resolve_probe_tenant(current_user, tenant_id)


@router.post("/api/erp/external/import/users")
async def import_external_users(
    request: _ImportUsersRequest,
    current_user: CurrentUser = Depends(require_permission(Resource.USERS, Action.ADMIN)),
):
    """把外部系统的账号导入为我方用户（幂等，按 external_user_id 增量同步）。

    - 已存在同 external_user_id 的用户 → 更新 username/display_name/role，**不动密码**
    - 不存在 → 新建，用 default_password 作为初始密码
    - 同租户下 username 撞车但 external_user_id 不同 → 跳过并回报，不静默覆盖
    """
    from database import hash_password

    tid = _resolve_import_tenant(current_user, request.tenant_id)
    if not request.users:
        return {"created": 0, "updated": 0, "skipped": [], "message": "没有要导入的用户"}
    if len(request.default_password) < 4:
        raise HTTPException(status_code=400, detail="初始密码长度至少 4 位")

    valid_roles = {"admin", "operate", "view"}
    now_dt = datetime.now()
    # bcrypt 很慢，整批共用一个初始密码，只算一次
    pw_hash = hash_password(request.default_password)

    created = updated = 0
    skipped: list[dict] = []

    with get_engine().begin() as sa_conn:
        for item in request.users:
            ext_id = (item.external_user_id or "").strip()
            username = (item.username or "").strip()
            if not ext_id or not username:
                skipped.append({"external_user_id": item.external_user_id,
                                "reason": "external_user_id 或 username 为空"})
                continue
            role = item.role if item.role in valid_roles else "operate"

            existing = sa_conn.execute(
                select(_t_users.c.id).where(and_(
                    _t_users.c.tenant_id == tid,
                    _t_users.c.external_user_id == ext_id,
                ))
            ).first()

            if existing:
                sa_conn.execute(
                    update(_t_users).where(_t_users.c.id == existing.id).values(
                        username=username,
                        display_name=item.display_name,
                        role=role,
                    )
                )
                updated += 1
                continue

            # 同租户重名但不是同一个外部账号：不覆盖，交给管理员决定
            clash = sa_conn.execute(
                select(_t_users.c.id, _t_users.c.external_user_id).where(and_(
                    _t_users.c.tenant_id == tid,
                    _t_users.c.username == username,
                ))
            ).first()
            if clash:
                skipped.append({
                    "external_user_id": ext_id,
                    "username": username,
                    "reason": "该租户下已存在同名用户且并非同一外部账号，未覆盖",
                })
                continue

            sa_conn.execute(
                insert(_t_users).values(
                    username=username,
                    password_hash=pw_hash,
                    role=role,
                    display_name=item.display_name,
                    tenant_id=tid,
                    external_user_id=ext_id,
                    created_by=current_user.id,
                    created_at=now_dt,
                )
            )
            created += 1

    logger.info(
        f"导入外部用户: tenant={tid} 新建={created} 更新={updated} 跳过={len(skipped)}"
        f"，操作人: {current_user.display_name}"
    )
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "message": f"导入完成：新建 {created} 个，更新 {updated} 个，跳过 {len(skipped)} 个",
    }


@router.post("/api/erp/external/import/warehouses")
async def import_external_warehouses(
    request: _ImportWarehousesRequest,
    current_user: CurrentUser = Depends(require_permission(Resource.WAREHOUSES, Action.ADMIN)),
):
    """把外部仓库导入为本地仓库行，**仅作权限锚点**。

    只在对方系统没有租户概念时才需要：那时仓库是唯一的作用域维度，
    而 user_warehouses 必须绑本地 warehouse_id。导入的行不承载任何库存数据，
    库存仍全部在对方。幂等：按 (tenant_id, external_warehouse_id) 增量同步。
    """
    tid = _resolve_import_tenant(current_user, request.tenant_id)
    if not request.warehouses:
        return {"created": 0, "updated": 0, "skipped": [], "message": "没有要导入的仓库"}

    created = updated = 0
    skipped: list[dict] = []

    with get_engine().begin() as sa_conn:
        for item in request.warehouses:
            ext_id = (item.external_warehouse_id or "").strip()
            name = (item.name or "").strip()
            if not ext_id or not name:
                skipped.append({"external_warehouse_id": item.external_warehouse_id,
                                "reason": "external_warehouse_id 或 name 为空"})
                continue

            existing = sa_conn.execute(
                select(_t_warehouses.c.id).where(and_(
                    _t_warehouses.c.tenant_id == tid,
                    _t_warehouses.c.external_warehouse_id == ext_id,
                ))
            ).first()
            if existing:
                sa_conn.execute(
                    update(_t_warehouses).where(_t_warehouses.c.id == existing.id)
                    .values(name=name)
                )
                updated += 1
                continue

            # slug 取外部编码（该租户内唯一），撞车说明本地已有同 slug 的仓库
            slug = ext_id[:64]
            clash = sa_conn.execute(
                select(_t_warehouses.c.id).where(and_(
                    _t_warehouses.c.tenant_id == tid,
                    _t_warehouses.c.slug == slug,
                ))
            ).first()
            if clash:
                skipped.append({
                    "external_warehouse_id": ext_id,
                    "reason": "该租户下已存在同 slug 的本地仓库，未覆盖",
                })
                continue

            sa_conn.execute(
                insert(_t_warehouses).values(
                    slug=slug,
                    name=name,
                    tenant_id=tid,
                    external_warehouse_id=ext_id,
                    is_default=0,
                    is_disabled=0,
                )
            )
            created += 1

    logger.info(
        f"导入外部仓库: tenant={tid} 新建={created} 更新={updated} 跳过={len(skipped)}"
        f"，操作人: {current_user.display_name}"
    )
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "message": f"导入完成：新建 {created} 个，更新 {updated} 个，跳过 {len(skipped)} 个",
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
