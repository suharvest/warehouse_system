"""MCP connection admin routes (extracted from app.py — Phase 3, task #7).

All 9 ``/api/mcp/connections...`` routes keep their full literal path (the
router is mounted without a prefix) so that the snapshot in
``tests/fixtures/route_inventory.json`` stays byte-for-byte identical.

The router depends only on shared primitives from ``deps.py``, ``db``,
``metadata`` and ``database``; the ``MCPProcessManager`` singleton is
resolved per-request via ``Depends(get_mcp_manager)`` so tests and multi-
worker deployments stay isolated.

Follow bare-module import style (no ``from backend.X``).
"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from database import generate_api_key, hash_api_key
from db import get_engine
from deps import (
    Action,
    CurrentUser,
    Resource,
    build_scope_predicates,
    check_warehouse_access,
    get_db,
    get_mcp_manager,
    load_or_404,
    require_permission,
    resolve_tenant_id_for_write,
    resolve_warehouse_id,
)
from metadata import (
    api_keys as _t_api_keys,
    mcp_connections as _t_mcp_connections,
    tenants as _t_tenants,
    warehouses as _t_warehouses,
)
from models import (
    CreateMCPConnectionRequest,
    MCPConnectionItem,
    MCPConnectionResponse,
    RoleName,
    UpdateMCPConnectionRequest,
)


router = APIRouter()

# Mirror of the constant previously defined in app.py. Re-derived here to
# keep this module free of imports from app.
_VALID_ROLE_VALUES = {r.value for r in RoleName}


# ============ MCP 连接管理 ============


def _build_connection_item(row, status_info: dict, warehouse_name: str = None, tenant_name: str = None) -> MCPConnectionItem:
    """从数据库行和实时状态构建响应对象"""
    return MCPConnectionItem(
        id=row['id'],
        name=row['name'],
        mcp_endpoint=row['mcp_endpoint'],
        role=row['role'] or RoleName.OPERATE.value,
        auto_start=bool(row['auto_start']),
        status=status_info.get('status', row['status'] or 'stopped'),
        websocket_status=status_info.get('websocket_status', 'not_started'),
        websocket_error=status_info.get('websocket_error'),
        error_message=status_info.get('error_message') or row['error_message'],
        restart_count=status_info.get('restart_count', row['restart_count'] or 0),
        debug_mode=bool(row.get('debug_mode', 0)),
        pid=status_info.get('pid'),
        uptime_seconds=status_info.get('uptime_seconds'),
        created_at=row['created_at'],
        updated_at=row['updated_at'],
        warehouse_id=row['warehouse_id'] if 'warehouse_id' in row.keys() else None,
        warehouse_name=warehouse_name,
        tenant_id=row['tenant_id'] if 'tenant_id' in row.keys() else None,
        tenant_name=tenant_name,
        device_id=row.get('device_id'),
    )


def _normalize_mcp_endpoint(endpoint: str) -> str:
    return (endpoint or '').strip()


def _ensure_unique_mcp_endpoint(endpoint: str, exclude_conn_id: Optional[str] = None) -> None:
    """Prevent duplicate local configs for the same cloud agent endpoint.

    SenseCraft/Xiaozhi endpoints identify one cloud-side agent entry. Multiple
    physical devices may be attached to that cloud agent, but the warehouse
    system only needs one local MCP connection for that endpoint.
    """
    if not endpoint:
        raise HTTPException(status_code=400, detail="云端链接不能为空")
    stmt = select(_t_mcp_connections.c.id, _t_mcp_connections.c.name).where(
        _t_mcp_connections.c.mcp_endpoint == endpoint
    )
    if exclude_conn_id is not None:
        stmt = stmt.where(_t_mcp_connections.c.id != exclude_conn_id)
    with get_engine().connect() as sa_conn:
        existing = sa_conn.execute(stmt).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"云端链接已被「{existing.name}」使用。同一个云端智能体入口只需配置一次。",
        )


@router.get("/api/mcp/connections")
async def list_mcp_connections(
    warehouse_id: Optional[int] = Query(None),
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """列出所有MCP连接（含实时状态）— Phase 2f: SA Core read."""
    wh_id = resolve_warehouse_id(current_user, warehouse_id)
    preds = list(build_scope_predicates(_t_mcp_connections, current_user.tenant_id, wh_id))
    stmt = (
        select(
            _t_mcp_connections.c.id, _t_mcp_connections.c.name,
            _t_mcp_connections.c.mcp_endpoint, _t_mcp_connections.c.api_key,
            _t_mcp_connections.c.role, _t_mcp_connections.c.auto_start,
            _t_mcp_connections.c.status, _t_mcp_connections.c.error_message,
            _t_mcp_connections.c.restart_count, _t_mcp_connections.c.debug_mode,
            _t_mcp_connections.c.created_at, _t_mcp_connections.c.updated_at,
            _t_mcp_connections.c.warehouse_id, _t_mcp_connections.c.tenant_id,
            _t_mcp_connections.c.device_id,
            _t_warehouses.c.name.label('warehouse_name'),
            _t_tenants.c.name.label('tenant_name'),
        )
        .select_from(
            _t_mcp_connections
            .outerjoin(_t_warehouses, _t_mcp_connections.c.warehouse_id == _t_warehouses.c.id)
            .outerjoin(_t_tenants, _t_mcp_connections.c.tenant_id == _t_tenants.c.id)
        )
        .order_by(
            _t_mcp_connections.c.tenant_id,
            _t_mcp_connections.c.warehouse_id,
            _t_mcp_connections.c.created_at.desc(),
        )
    )
    if preds:
        stmt = stmt.where(and_(*preds))
    with get_engine().connect() as sa_conn:
        rows = sa_conn.execute(stmt).fetchall()

    items = []
    for r in rows:
        row_dict = dict(r._mapping)
        status_info = mcp_manager.get_connection_status(row_dict['id'])
        items.append(_build_connection_item(
            row_dict, status_info,
            warehouse_name=row_dict.get('warehouse_name'),
            tenant_name=row_dict.get('tenant_name'),
        ))

    return items


@router.post("/api/mcp/connections", response_model=MCPConnectionResponse)
async def create_mcp_connection(
    request: CreateMCPConnectionRequest,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """创建MCP连接（自动创建关联的API Key）"""
    conn_id = str(uuid.uuid4())[:8]
    now_dt = datetime.now()
    now = now_dt.isoformat()
    mcp_endpoint = _normalize_mcp_endpoint(request.mcp_endpoint)
    _ensure_unique_mcp_endpoint(mcp_endpoint)

    # 验证角色
    role = request.role if request.role in _VALID_ROLE_VALUES else RoleName.OPERATE.value

    # Legacy compatibility: older callers may still send device_id. New UI does
    # not collect it; mcp_endpoint is the cloud-agent identity at this layer.
    raw_device_id = request.device_id.strip() if request.device_id else None
    device_id = raw_device_id if raw_device_id else None

    # Legacy device-id de-duplication for API callers that still pass it.
    if device_id:
        with get_engine().connect() as sa_conn:
            existing = sa_conn.execute(
                select(_t_mcp_connections.c.id, _t_mcp_connections.c.name)
                .where(_t_mcp_connections.c.device_id == device_id)
            ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"设备 ID {device_id} 已被「{existing.name}」注册，一个设备只能注册一次",
            )

    # 自动生成 API Key
    api_key_plain = generate_api_key()
    key_hash = hash_api_key(api_key_plain)

    with get_db() as conn:
        wh_id = request.warehouse_id
        if wh_id is not None:
            wh_id = resolve_warehouse_id(current_user, wh_id)
            check_warehouse_access(conn, current_user, wh_id)
        conn_tenant_id = resolve_tenant_id_for_write(current_user, wh_id)

    with get_engine().begin() as sa_conn:
        # 创建关联的 API Key（is_system=1，不在用户管理中显示）
        sa_conn.execute(
            insert(_t_api_keys).values(
                key_hash=key_hash,
                name=f'Agent: {request.name}',
                role=role,
                user_id=current_user.id,
                is_system=1,
                warehouse_id=wh_id,
                tenant_id=conn_tenant_id,
                created_at=now_dt,
            )
        )
        # 创建 MCP 连接记录
        try:
            sa_conn.execute(
                insert(_t_mcp_connections).values(
                    id=conn_id,
                    name=request.name,
                    mcp_endpoint=mcp_endpoint,
                    api_key=api_key_plain,
                    role=role,
                    auto_start=1 if request.auto_start else 0,
                    warehouse_id=wh_id,
                    tenant_id=conn_tenant_id,
                    device_id=device_id,
                    status='stopped',
                    created_at=now,
                    updated_at=now,
                )
            )
        except IntegrityError:
            raise HTTPException(
                status_code=409,
                detail=f"设备 ID {device_id} 已被其他连接注册，一个设备只能注册一次",
            )

    # 如果 auto_start，立即启动
    if request.auto_start:
        await mcp_manager.start_connection(conn_id, mcp_endpoint, api_key_plain)
        status_info = mcp_manager.get_connection_status(conn_id)
        with get_engine().begin() as sa_conn:
            sa_conn.execute(
                update(_t_mcp_connections)
                .where(_t_mcp_connections.c.id == conn_id)
                .values(status=status_info['status'], updated_at=datetime.now().isoformat())
            )

    # 获取创建的记录
    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_mcp_connections).where(_t_mcp_connections.c.id == conn_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    status_info = mcp_manager.get_connection_status(conn_id)
    return MCPConnectionResponse(
        success=True,
        message="连接已创建",
        connection=_build_connection_item(row, status_info)
    )


@router.put("/api/mcp/connections/{conn_id}", response_model=MCPConnectionResponse)
async def update_mcp_connection(
    conn_id: str,
    request: UpdateMCPConnectionRequest,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """修改MCP连接配置"""
    with get_engine().connect() as sa_conn:
        _r = load_or_404(
            sa_conn, _t_mcp_connections, conn_id,
            not_found="连接不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权访问其他租户的MCP连接",
        )
    row = dict(_r._mapping)

    old_endpoint = row['mcp_endpoint']
    key_hash = hash_api_key(row['api_key'])
    new_endpoint = None
    if request.mcp_endpoint is not None:
        new_endpoint = _normalize_mcp_endpoint(request.mcp_endpoint)
        _ensure_unique_mcp_endpoint(new_endpoint, exclude_conn_id=conn_id)

    # Resolve warehouse access (helper retains ``conn`` arg for signature
    # compatibility but reads via SA Core internally).
    new_wh_id = None
    new_tenant_id = None
    if request.warehouse_id is not None:
        new_wh_id = resolve_warehouse_id(current_user, request.warehouse_id)
        check_warehouse_access(None, current_user, new_wh_id)
        new_tenant_id = resolve_tenant_id_for_write(current_user, new_wh_id)

    mcp_values = {}
    apikey_values = {}
    if request.name is not None:
        mcp_values['name'] = request.name
        apikey_values['name'] = f'Agent: {request.name}'
    if request.mcp_endpoint is not None:
        mcp_values['mcp_endpoint'] = new_endpoint
    if request.role is not None and request.role in _VALID_ROLE_VALUES:
        mcp_values['role'] = request.role
        apikey_values['role'] = request.role
    if request.auto_start is not None:
        mcp_values['auto_start'] = 1 if request.auto_start else 0
    if request.warehouse_id is not None:
        mcp_values['warehouse_id'] = new_wh_id
        mcp_values['tenant_id'] = new_tenant_id
        apikey_values['warehouse_id'] = new_wh_id
        apikey_values['tenant_id'] = new_tenant_id
    if request.device_id is not None:
        # Legacy compatibility: strip 后空字符串视为解除绑定（传 NULL）
        raw = request.device_id.strip()
        new_device_id = raw if raw else None

        # Legacy device-id de-duplication（排除自身）
        if new_device_id:
            with get_engine().connect() as sa_conn:
                existing = sa_conn.execute(
                    select(_t_mcp_connections.c.id, _t_mcp_connections.c.name)
                    .where(
                        and_(
                            _t_mcp_connections.c.device_id == new_device_id,
                            _t_mcp_connections.c.id != conn_id,
                        )
                    )
                ).first()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"设备 ID {new_device_id} 已被「{existing.name}」注册，一个设备只能注册一次",
                )
        mcp_values['device_id'] = new_device_id

    if mcp_values:
        mcp_values['updated_at'] = datetime.now().isoformat()
        with get_engine().begin() as sa_conn:
            if apikey_values:
                sa_conn.execute(
                    update(_t_api_keys)
                    .where(_t_api_keys.c.key_hash == key_hash)
                    .values(**apikey_values)
                )
            try:
                sa_conn.execute(
                    update(_t_mcp_connections)
                    .where(_t_mcp_connections.c.id == conn_id)
                    .values(**mcp_values)
                )
            except IntegrityError:
                raise HTTPException(
                    status_code=409,
                    detail=f"设备 ID {new_device_id} 已被其他连接注册，一个设备只能注册一次",
                )

    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_mcp_connections).where(_t_mcp_connections.c.id == conn_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    if new_endpoint is not None and new_endpoint != old_endpoint:
        if (conn_id in mcp_manager.connections
            and mcp_manager.connections[conn_id].process
            and mcp_manager.connections[conn_id].process.returncode is None):
            await mcp_manager.restart_connection(conn_id, row['mcp_endpoint'], row['api_key'])

    status_info = mcp_manager.get_connection_status(conn_id)
    return MCPConnectionResponse(
        success=True,
        message="连接已更新",
        connection=_build_connection_item(row, status_info)
    )


@router.delete("/api/mcp/connections/{conn_id}")
async def delete_mcp_connection(
    conn_id: str,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """删除MCP连接（先停止）"""
    with get_engine().connect() as sa_conn:
        _r = load_or_404(
            sa_conn, _t_mcp_connections, conn_id,
            not_found="连接不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权访问其他租户的MCP连接",
        )
    row = dict(_r._mapping)

    # 先停止进程
    await mcp_manager.stop_connection(conn_id)
    mcp_manager.remove_connection(conn_id)

    # 删除数据库记录及关联的 API Key
    api_key_plain = row['api_key']
    with get_engine().begin() as sa_conn:
        if api_key_plain:
            key_hash = hash_api_key(api_key_plain)
            sa_conn.execute(delete(_t_api_keys).where(_t_api_keys.c.key_hash == key_hash))
        sa_conn.execute(delete(_t_mcp_connections).where(_t_mcp_connections.c.id == conn_id))

    return {"success": True, "message": "连接已删除"}


@router.post("/api/mcp/connections/{conn_id}/start", response_model=MCPConnectionResponse)
async def start_mcp_connection(
    conn_id: str,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """启动MCP连接"""
    with get_engine().connect() as sa_conn:
        _r = load_or_404(
            sa_conn, _t_mcp_connections, conn_id,
            not_found="连接不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权访问其他租户的MCP连接",
        )
    row = dict(_r._mapping)

    success = await mcp_manager.start_connection(
        conn_id, row['mcp_endpoint'], row['api_key'],
        debug_mode=bool(row.get('debug_mode', 0))
    )

    status_info = mcp_manager.get_connection_status(conn_id)
    with get_engine().begin() as sa_conn:
        sa_conn.execute(
            update(_t_mcp_connections)
            .where(_t_mcp_connections.c.id == conn_id)
            .values(
                status=status_info['status'],
                error_message=status_info.get('error_message'),
                restart_count=0,
                updated_at=datetime.now().isoformat(),
            )
        )
    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_mcp_connections).where(_t_mcp_connections.c.id == conn_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    return MCPConnectionResponse(
        success=success,
        message="连接已启动" if success else "启动失败",
        connection=_build_connection_item(row, status_info)
    )


@router.post("/api/mcp/connections/{conn_id}/stop", response_model=MCPConnectionResponse)
async def stop_mcp_connection(
    conn_id: str,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """停止MCP连接"""
    with get_engine().connect() as sa_conn:
        _r = load_or_404(
            sa_conn, _t_mcp_connections, conn_id,
            not_found="连接不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权访问其他租户的MCP连接",
        )
    row = dict(_r._mapping)

    await mcp_manager.stop_connection(conn_id)

    status_info = mcp_manager.get_connection_status(conn_id)
    with get_engine().begin() as sa_conn:
        sa_conn.execute(
            update(_t_mcp_connections)
            .where(_t_mcp_connections.c.id == conn_id)
            .values(status='stopped', error_message=None, updated_at=datetime.now().isoformat())
        )
    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_mcp_connections).where(_t_mcp_connections.c.id == conn_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    return MCPConnectionResponse(
        success=True,
        message="连接已停止",
        connection=_build_connection_item(row, status_info)
    )


@router.post("/api/mcp/connections/{conn_id}/restart", response_model=MCPConnectionResponse)
async def restart_mcp_connection(
    conn_id: str,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """重启MCP连接"""
    with get_engine().connect() as sa_conn:
        _r = load_or_404(
            sa_conn, _t_mcp_connections, conn_id,
            not_found="连接不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权访问其他租户的MCP连接",
        )
    row = dict(_r._mapping)

    success = await mcp_manager.restart_connection(
        conn_id, row['mcp_endpoint'], row['api_key']
    )

    status_info = mcp_manager.get_connection_status(conn_id)
    with get_engine().begin() as sa_conn:
        sa_conn.execute(
            update(_t_mcp_connections)
            .where(_t_mcp_connections.c.id == conn_id)
            .values(
                status=status_info['status'],
                error_message=status_info.get('error_message'),
                restart_count=0,
                updated_at=datetime.now().isoformat(),
            )
        )
    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_mcp_connections).where(_t_mcp_connections.c.id == conn_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    return MCPConnectionResponse(
        success=success,
        message="连接已重启" if success else "重启失败",
        connection=_build_connection_item(row, status_info)
    )


@router.post("/api/mcp/connections/{conn_id}/debug", response_model=MCPConnectionResponse)
async def toggle_mcp_debug(
    conn_id: str,
    request: Request,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """切换MCP连接的调试模式"""
    body = await request.json()
    enable = bool(body.get('enable', False))

    with get_engine().connect() as sa_conn:
        _r = load_or_404(
            sa_conn, _t_mcp_connections, conn_id,
            not_found="连接不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权访问其他租户的MCP连接",
        )
    row = dict(_r._mapping)

    # 更新 DB
    with get_engine().begin() as sa_conn:
        sa_conn.execute(
            update(_t_mcp_connections)
            .where(_t_mcp_connections.c.id == conn_id)
            .values(debug_mode=1 if enable else 0, updated_at=datetime.now().isoformat())
        )

    # 如果进程正在运行，重启以应用新设置
    if conn_id in mcp_manager.connections and mcp_manager.connections[conn_id].status == 'running':
        success = await mcp_manager.toggle_debug(conn_id, row['mcp_endpoint'], row['api_key'], enable)
    else:
        success = True

    status_info = mcp_manager.get_connection_status(conn_id)
    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_mcp_connections).where(_t_mcp_connections.c.id == conn_id)
        ).first()
    row = dict(_r._mapping) if _r else None

    return MCPConnectionResponse(
        success=success,
        message=f"调试模式已{'开启' if enable else '关闭'}",
        connection=_build_connection_item(row, status_info)
    )


@router.get("/api/mcp/connections/{conn_id}/logs")
async def get_mcp_connection_logs(
    conn_id: str,
    lines: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
    mcp_manager = Depends(get_mcp_manager),
):
    """获取MCP连接的最近日志"""
    with get_engine().connect() as sa_conn:
        row = load_or_404(
            sa_conn, _t_mcp_connections, conn_id,
            columns=[_t_mcp_connections.c.id, _t_mcp_connections.c.tenant_id],
            not_found="连接不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权访问其他租户的MCP连接",
        )

    logs = mcp_manager.get_logs(conn_id, lines)
    return {"logs": logs}
