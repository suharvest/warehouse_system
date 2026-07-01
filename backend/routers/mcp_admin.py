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
import base64
import uuid
from datetime import datetime
from typing import Optional

import httpx
import numpy as np
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
    mcp_agent_devices as _t_mcp_agent_devices,
    mcp_connections as _t_mcp_connections,
    tenants as _t_tenants,
    warehouses as _t_warehouses,
)
from models import (
    CreateMCPConnectionRequest,
    MCPAgentDeviceCreateRequest,
    MCPAgentDeviceUpdateRequest,
    MCPConnectionItem,
    MCPConnectionResponse,
    RoleName,
    UpdateMCPConnectionRequest,
)


router = APIRouter()

# Mirror of the constant previously defined in app.py. Re-derived here to
# keep this module free of imports from app.
_VALID_ROLE_VALUES = {r.value for r in RoleName}

# xiaozhi 设备 httpd 固件写死监听 80 端口，不由用户配置。
DEVICE_HTTP_PORT = 80


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
        # 兜底绑定仓库：agent key 是 operate 角色，若 warehouse_id 为空会触发
        # build_authorized_scope_predicates 的“无授权仓库 → false()”，导致该 agent
        # 查任何物料都返回空（智能体表现为“物料不存在”）。未显式指定仓库时，绑定到
        # 该租户的默认仓库，保证 agent key 始终有可访问的仓库作用域。
        if wh_id is None:
            wh_id = sa_conn.execute(
                select(_t_warehouses.c.id)
                .where(and_(
                    _t_warehouses.c.tenant_id == conn_tenant_id,
                    _t_warehouses.c.is_default == 1,
                ))
                .order_by(_t_warehouses.c.id.asc())
            ).scalar()

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
        # 防御性 tenant 约束：load_or_404 已检过一次，UPDATE 再加一层防
        # 行被并发 reparent / load 路径有 bug 时的越租户写入
        mcp_where = [_t_mcp_connections.c.id == conn_id]
        if current_user.tenant_id is not None:
            mcp_where.append(_t_mcp_connections.c.tenant_id == current_user.tenant_id)
        with get_engine().begin() as sa_conn:
            if apikey_values:
                # api_keys.key_hash 是全局唯一索引（metadata.py），且 key_hash 来自
                # 已通过 tenant scope 校验的 mcp_connections 行，无需重复约束
                sa_conn.execute(
                    update(_t_api_keys)
                    .where(_t_api_keys.c.key_hash == key_hash)
                    .values(**apikey_values)
                )
            try:
                res = sa_conn.execute(
                    update(_t_mcp_connections)
                    .where(and_(*mcp_where))
                    .values(**mcp_values)
                )
            except IntegrityError:
                raise HTTPException(
                    status_code=409,
                    detail=f"设备 ID {new_device_id} 已被其他连接注册，一个设备只能注册一次",
                )
            if res.rowcount != 1:
                raise HTTPException(status_code=403, detail="无权访问其他租户的MCP连接")

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
    # 防御性 tenant 约束：同 update 路径
    mcp_where = [_t_mcp_connections.c.id == conn_id]
    if current_user.tenant_id is not None:
        mcp_where.append(_t_mcp_connections.c.tenant_id == current_user.tenant_id)
    with get_engine().begin() as sa_conn:
        if api_key_plain:
            key_hash = hash_api_key(api_key_plain)
            sa_conn.execute(delete(_t_api_keys).where(_t_api_keys.c.key_hash == key_hash))
        res = sa_conn.execute(delete(_t_mcp_connections).where(and_(*mcp_where)))
        if res.rowcount != 1:
            raise HTTPException(status_code=403, detail="无权访问其他租户的MCP连接")

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


# ============ 智能体下挂的物理设备（一对多子表）============
#
# 一个 mcp_connection（云端智能体端点）下可挂多个物理设备，每个设备配 LAN IP，
# 供后续"云端下发人脸库到设备"使用。权限/租户隔离与上方连接 CRUD 完全一致：
# require_permission(Resource.MCP, Action.ADMIN) + load_or_404 校验 conn 属于当前租户。


def _assert_conn_in_tenant(conn_id: str, current_user: CurrentUser):
    """校验目标连接存在且属于当前租户；否则 404/403（与连接 CRUD 同语义）。

    返回加载到的连接行（含 id/tenant_id），供下游需要 tenant_id 的逻辑复用。
    """
    with get_engine().connect() as sa_conn:
        return load_or_404(
            sa_conn, _t_mcp_connections, conn_id,
            columns=[_t_mcp_connections.c.id, _t_mcp_connections.c.tenant_id],
            not_found="连接不存在",
            tenant_id=current_user.tenant_id,
            forbidden="无权访问其他租户的MCP连接",
        )


def _serialize_device(row) -> dict:
    d = dict(row._mapping)
    d['face_enabled'] = bool(d.get('face_enabled'))
    return d


def _validate_device_fields(ip, port):
    """ip 非空、port 1-65535。返回归一化后的 (ip, port)。"""
    ip = (ip or '').strip()
    if not ip:
        raise HTTPException(status_code=400, detail="设备 IP 不能为空")
    if port is None:
        port = 80
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise HTTPException(status_code=400, detail="端口号必须在 1-65535 之间")
    return ip, port


def _ensure_device_id_unique(conn_id: str, device_id: str, exclude_dev_id: Optional[int] = None) -> None:
    """同一 connection 下 device_id 不可重复（device_id 为空不校验）。"""
    if not device_id:
        return
    stmt = select(_t_mcp_agent_devices.c.id).where(
        and_(
            _t_mcp_agent_devices.c.connection_id == conn_id,
            _t_mcp_agent_devices.c.device_id == device_id,
        )
    )
    if exclude_dev_id is not None:
        stmt = stmt.where(_t_mcp_agent_devices.c.id != exclude_dev_id)
    with get_engine().connect() as sa_conn:
        existing = sa_conn.execute(stmt).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"设备标识 {device_id} 在该智能体下已存在，不可重复",
        )


def _load_device_or_404(conn_id: str, dev_id: int):
    with get_engine().connect() as sa_conn:
        _r = sa_conn.execute(
            select(_t_mcp_agent_devices).where(
                and_(
                    _t_mcp_agent_devices.c.id == dev_id,
                    _t_mcp_agent_devices.c.connection_id == conn_id,
                )
            )
        ).first()
    if _r is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    return _r


@router.get("/api/mcp/connections/{conn_id}/devices")
async def list_mcp_agent_devices(
    conn_id: str,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
):
    """列出某智能体下挂的所有物理设备。"""
    _assert_conn_in_tenant(conn_id, current_user)
    with get_engine().connect() as sa_conn:
        rows = sa_conn.execute(
            select(_t_mcp_agent_devices)
            .where(_t_mcp_agent_devices.c.connection_id == conn_id)
            .order_by(_t_mcp_agent_devices.c.id.asc())
        ).fetchall()
    return [_serialize_device(r) for r in rows]


@router.post("/api/mcp/connections/{conn_id}/devices")
async def create_mcp_agent_device(
    conn_id: str,
    request: MCPAgentDeviceCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
):
    """为某智能体新增一个物理设备。"""
    _assert_conn_in_tenant(conn_id, current_user)
    ip, port = _validate_device_fields(request.ip, request.port)
    device_id = request.device_id.strip() if request.device_id else None
    _ensure_device_id_unique(conn_id, device_id)

    now = datetime.now().isoformat()
    with get_engine().begin() as sa_conn:
        try:
            res = sa_conn.execute(
                insert(_t_mcp_agent_devices).values(
                    connection_id=conn_id,
                    device_id=device_id,
                    name=(request.name.strip() if request.name else None),
                    ip=ip,
                    port=port,
                    model_tag=(request.model_tag.strip() if request.model_tag else None),
                    # face_enabled 列已废弃（deprecated）：不再读写入参，DB 列 NOT NULL
                    # server_default '0' 会自动填 0，省略即可。
                    last_seen=request.last_seen,
                    created_at=now,
                    updated_at=now,
                )
            )
        except IntegrityError:
            raise HTTPException(
                status_code=409,
                detail=f"设备标识 {device_id} 在该智能体下已存在，不可重复",
            )
    new_id = res.inserted_primary_key[0]
    row = _load_device_or_404(conn_id, new_id)
    return {"success": True, "message": "设备已添加", "device": _serialize_device(row)}


@router.put("/api/mcp/connections/{conn_id}/devices/{dev_id}")
async def update_mcp_agent_device(
    conn_id: str,
    dev_id: int,
    request: MCPAgentDeviceUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
):
    """更新某智能体下挂的物理设备（仅更新提供的字段）。"""
    _assert_conn_in_tenant(conn_id, current_user)
    existing = _load_device_or_404(conn_id, dev_id)
    cur = dict(existing._mapping)

    values = {}
    if request.device_id is not None:
        new_device_id = request.device_id.strip() or None
        _ensure_device_id_unique(conn_id, new_device_id, exclude_dev_id=dev_id)
        values['device_id'] = new_device_id
    if request.name is not None:
        values['name'] = request.name.strip() or None
    if request.ip is not None or request.port is not None:
        # 任一改动都要保证 (ip, port) 仍合法；用现有值兜底未提供的一方。
        ip = request.ip if request.ip is not None else cur.get('ip')
        port = request.port if request.port is not None else cur.get('port')
        ip, port = _validate_device_fields(ip, port)
        values['ip'] = ip
        values['port'] = port
    if request.model_tag is not None:
        values['model_tag'] = request.model_tag.strip() or None
    # face_enabled 列已废弃（deprecated）：不再读写入参，忽略任何传入值。
    if request.last_seen is not None:
        values['last_seen'] = request.last_seen

    if values:
        values['updated_at'] = datetime.now().isoformat()
        with get_engine().begin() as sa_conn:
            try:
                sa_conn.execute(
                    update(_t_mcp_agent_devices)
                    .where(
                        and_(
                            _t_mcp_agent_devices.c.id == dev_id,
                            _t_mcp_agent_devices.c.connection_id == conn_id,
                        )
                    )
                    .values(**values)
                )
            except IntegrityError:
                raise HTTPException(
                    status_code=409,
                    detail="设备标识在该智能体下已存在，不可重复",
                )

    row = _load_device_or_404(conn_id, dev_id)
    return {"success": True, "message": "设备已更新", "device": _serialize_device(row)}


@router.delete("/api/mcp/connections/{conn_id}/devices/{dev_id}")
async def delete_mcp_agent_device(
    conn_id: str,
    dev_id: int,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
):
    """删除某智能体下挂的物理设备。"""
    _assert_conn_in_tenant(conn_id, current_user)
    _load_device_or_404(conn_id, dev_id)
    with get_engine().begin() as sa_conn:
        sa_conn.execute(
            delete(_t_mcp_agent_devices).where(
                and_(
                    _t_mcp_agent_devices.c.id == dev_id,
                    _t_mcp_agent_devices.c.connection_id == conn_id,
                )
            )
        )
    return {"success": True, "message": "设备已删除"}


# ============ 云端下发人脸库到设备 ============
#
# 取本租户人脸库，按固定模型标签过滤（不同模型 embedding 在不同向量空间，
# 混下去会污染设备本地匹配），HTTP POST 到设备 LAN 上的 batch-update 端点。
# 本期同 LAN 直推、无 token；鉴权/租户隔离照搬设备 CRUD。

# 推送到设备的 HTTP 超时（秒）。设备在 LAN 内，10s 足够；超时即判定不可达。
_PUSH_FACES_TIMEOUT = 10.0

# 单台设备下发人脸数上限。与固件 FACE_MAX_COUNT=20 对齐：超出固件存储能力，
# 必须在服务端拒绝，绝不截断或盲推。
MAX_PUSH_FACES = 20

# 设备固件写死的 embedding 模型标签。当前全设备同一模型，下发时用此固定常量
# 过滤人脸库并作为 payload.model_tag，**不读** mcp_agent_devices.model_tag（该列
# 保留作未来多模型扩展，现阶段不对用户暴露、不参与下发）。
DEVICE_FACE_MODEL_TAG = "we2-mfn128-v1"

# ---- Embedding 量化（仅 push 路径） -------------------------------------
#
# DB（face_enrollments.embedding）与 /api/face/library 始终是 **canonical 满精度
# 源**：512 字节 float32 LE（128 维）。下发到设备时为省带宽/设备存储做量化，
# 量化**只在此 push 路径发生**，不回写 DB、不改 library 默认输出。
#
# 线缆契约：payload 带 ``embedding_format`` 字段，设备据此解码：
#   - "float32": 每条 512 字节 = 128 × IEEE-754 binary32 LE（原样，不量化）
#   - "fp16":    每条 256 字节 = 128 × IEEE-754 binary16 LE
#   - "int8":    （未来）每条 128 字节 + per-vector scale，尚未实现
#
# 一处切换：改 DEVICE_EMBEDDING_FORMAT 即可切换全设备下发格式（设备侧需同步）。
DEVICE_EMBEDDING_FORMAT = "fp16"


def quantize_embedding(f32_bytes: bytes, fmt: str) -> bytes:
    """把 canonical float32 embedding 字节量化为目标线缆格式的字节。

    可插拔 dispatch 设计：新增量化格式只需在此加一个分支并扩展上面的契约
    注释 + 设备侧解码，push 路径与测试无需改动。

    Args:
        f32_bytes: 128 × IEEE-754 binary32 LE = 512 字节（DB canonical 源）。
        fmt: 目标格式，见 DEVICE_EMBEDDING_FORMAT 注释。

    Returns:
        量化后的原始字节（小端）。

    Raises:
        NotImplementedError: 目标格式尚未实现（如 int8）。
        ValueError: 未知格式。
    """
    if fmt == "float32":
        # 原样透传：canonical 源已是 float32 LE。
        return f32_bytes
    if fmt == "fp16":
        vec = np.frombuffer(f32_bytes, dtype="<f4")
        return vec.astype("<f2").tobytes()
    if fmt == "int8":
        # 占位：int8 量化需要 per-vector scale（如 max(abs) / 127）随每条
        # embedding 一起下发，设备侧反量化时 dequant = q.astype(f32) * scale。
        # 线缆契约届时需扩展 face 项（embedding_b64=128 字节 + scale 字段）。
        raise NotImplementedError(
            "int8 量化尚未实现：需要 per-vector scale 且线缆契约要带 scale 字段"
        )
    raise ValueError(f"未知 embedding_format: {fmt!r}")


@router.post("/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
async def push_faces_to_device(
    conn_id: str,
    dev_id: int,
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
):
    """把本租户人脸库（按固定模型标签过滤）下发到该物理设备。

    配置类错误（缺 IP）返回 4xx；人脸库超过设备上限返回 success:false；设备侧网络错误
    （不可达 / 超时 / 4xx-5xx）以 ``{"success": false, "error": ...}`` 返回
    200，让前端能展示失败原因而不是静默成功。
    """
    conn_row = _assert_conn_in_tenant(conn_id, current_user)
    dev = dict(_load_device_or_404(conn_id, dev_id)._mapping)

    # face_enabled gate 已移除：任何有 IP 的设备都可手动下发。
    ip = (dev.get("ip") or "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="设备缺少 IP，无法下发")
    port = DEVICE_HTTP_PORT  # 固件写死 80，不读 device.port
    # 固定模型标签：全设备同模型，不读 mcp_agent_devices.model_tag。
    model_tag = DEVICE_FACE_MODEL_TAG

    # 取本租户人脸库并按固定 model_tag 过滤（复用 face 路由的 library 逻辑）。
    from routers.face import build_face_library
    tid = conn_row.tenant_id if hasattr(conn_row, "tenant_id") else dict(conn_row._mapping).get("tenant_id")
    library = build_face_library(tid, model_tag=model_tag)
    # 服务端强制上限：超过设备固件容量（FACE_MAX_COUNT=20）直接拒绝，不 POST、
    # 不截断。错误信息用户友好，前端会 alert 出来。
    if len(library) > MAX_PUSH_FACES:
        return {
            "success": False,
            "error": f"人脸库共 {len(library)} 张，超过设备上限 {MAX_PUSH_FACES} 张，请减少启用的人脸后再下发",
        }
    # build_face_library 返回 canonical float32 的 embedding_b64；下发前按
    # DEVICE_EMBEDDING_FORMAT 量化（解码 → 量化 → 重新 base64），library/DB 不变。
    fmt = DEVICE_EMBEDDING_FORMAT
    faces = []
    for e in library:
        f32_bytes = base64.b64decode(e["embedding_b64"])
        emb_bytes = quantize_embedding(f32_bytes, fmt)
        faces.append({
            "name": e["name"],
            "subject_id": e["subject_id"],
            "embedding_b64": base64.b64encode(emb_bytes).decode(),
        })
    payload = {"model_tag": model_tag, "embedding_format": fmt, "faces": faces}

    url = f"http://{ip}:{port}/api/face/batch-update"
    # trust_env=False：设备在 LAN 内，必须直连其 IP，绝不能走系统/环境代理
    # （走代理会把局域网地址发到外网代理，返回误导性的 4xx/5xx 或超时）。
    try:
        async with httpx.AsyncClient(timeout=_PUSH_FACES_TIMEOUT, trust_env=False) as client:
            resp = await client.post(url, json=payload)
    except httpx.TimeoutException:
        return {"success": False, "error": f"设备响应超时（{int(_PUSH_FACES_TIMEOUT)}s）：{url}"}
    except httpx.RequestError as e:
        return {"success": False, "error": f"无法连接设备 {url}：{e}"}

    if resp.status_code >= 400:
        return {
            "success": False,
            "error": f"设备返回 HTTP {resp.status_code}: {resp.text[:300]}",
        }
    try:
        device_response = resp.json()
    except Exception:
        device_response = resp.text[:300]
    return {
        "success": True,
        "pushed_count": len(faces),
        "model_tag": model_tag,
        "device_response": device_response,
    }
