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
import ipaddress
import os
import secrets as _secrets
import socket as _socket
import uuid
from datetime import datetime
from typing import Optional

import httpx
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, delete, insert, or_, select, update
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
    tenant_face_config as _t_tenant_face_config,
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


def _ensure_unique_mcp_endpoint(
    endpoint: str,
    *,
    caller_tenant_id: Optional[int],
    exclude_conn_id: Optional[str] = None,
) -> None:
    """Prevent duplicate local configs for the same cloud agent endpoint.

    SenseCraft/Xiaozhi endpoints identify one cloud-side agent entry. Multiple
    physical devices may be attached to that cloud agent, but the warehouse
    system only needs one local MCP connection for that endpoint.

    Uniqueness is intentionally global (one cloud entry = one local config),
    but the conflicting connection's name is only revealed to callers of the
    same tenant — cross-tenant the 409 stays generic to avoid leaking names.
    """
    if not endpoint:
        raise HTTPException(status_code=400, detail="云端链接不能为空")
    stmt = select(
        _t_mcp_connections.c.id,
        _t_mcp_connections.c.name,
        _t_mcp_connections.c.tenant_id,
    ).where(_t_mcp_connections.c.mcp_endpoint == endpoint)
    if exclude_conn_id is not None:
        stmt = stmt.where(_t_mcp_connections.c.id != exclude_conn_id)
    with get_engine().connect() as sa_conn:
        existing = sa_conn.execute(stmt).first()
    if existing:
        same_tenant = caller_tenant_id is None or existing.tenant_id == caller_tenant_id
        detail = (
            f"云端链接已被「{existing.name}」使用。同一个云端智能体入口只需配置一次。"
            if same_tenant
            else "云端链接已被其他租户使用。同一个云端智能体入口只需配置一次。"
        )
        raise HTTPException(status_code=409, detail=detail)


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
    _ensure_unique_mcp_endpoint(mcp_endpoint, caller_tenant_id=current_user.tenant_id)

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
        _ensure_unique_mcp_endpoint(
            new_endpoint,
            exclude_conn_id=conn_id,
            caller_tenant_id=current_user.tenant_id,
        )

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
    """ip 必须是合法 IP 字面量且不在危险网段、port 1-65535。返回归一化后的 (ip, port)。

    设备 ip 会被后端直接用于服务端 HTTP 请求（人脸下发等），必须挡住 SSRF 面：
    回环（本机管理端点）、链路本地（169.254.169.254 云元数据）、组播/保留/未指定
    一律拒绝。私网与公网地址均放行——设备既可能在 LAN 也可能有可路由地址。
    """
    ip = (ip or '').strip()
    if not ip:
        raise HTTPException(status_code=400, detail="设备 IP 不能为空")
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=400, detail="设备 IP 必须是合法的 IP 地址")
    # MCP_DEVICE_ALLOW_LOOPBACK=1：显式豁免回环（本机跑设备模拟器的开发/测试场景）
    allow_loopback = os.environ.get("MCP_DEVICE_ALLOW_LOOPBACK") == "1"
    if (
        (addr.is_loopback and not allow_loopback)
        or addr.is_link_local or addr.is_multicast
        or addr.is_unspecified or addr.is_reserved
    ):
        raise HTTPException(status_code=400, detail="设备 IP 不在允许范围（禁止回环/链路本地/组播/保留地址）")
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


@router.get("/api/mcp/agent-devices")
async def list_all_agent_devices(
    current_user: CurrentUser = Depends(require_permission(Resource.MCP, Action.ADMIN)),
):
    """扁平列出本租户所有物理设备（join 智能体连接拿名称）。

    供「人脸库下发」的设备选择器用：一处拿到全部设备，无需先遍历每个智能体。
    租户隔离与设备 CRUD 一致——按所属 mcp_connection 的 tenant_id 过滤；全局
    管理员（tenant_id 为 None）不加租户约束，可见所有设备。
    """
    stmt = (
        select(
            _t_mcp_agent_devices.c.id,
            _t_mcp_agent_devices.c.name,
            _t_mcp_agent_devices.c.ip,
            _t_mcp_agent_devices.c.connection_id,
            _t_mcp_connections.c.name.label("connection_name"),
        )
        .select_from(
            _t_mcp_agent_devices.join(
                _t_mcp_connections,
                _t_mcp_agent_devices.c.connection_id == _t_mcp_connections.c.id,
            )
        )
        .order_by(_t_mcp_connections.c.name.asc(), _t_mcp_agent_devices.c.id.asc())
    )
    if current_user.tenant_id is not None:
        stmt = stmt.where(_t_mcp_connections.c.tenant_id == current_user.tenant_id)
    with get_engine().connect() as sa_conn:
        rows = sa_conn.execute(stmt).fetchall()
    return [
        {
            "connection_id": r.connection_id,
            "connection_name": r.connection_name,
            "id": r.id,
            "name": r.name,
            "ip": r.ip,
        }
        for r in rows
    ]


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
DEVICE_FACE_MODEL_TAG = "we2-mfnr6-128-v1"

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


def _device_facing_base_url(device_ip: str) -> str:
    """lan 模式下发给设备的识别代理 base（设备会拼 /recognize）。

    优先 env ``WAREHOUSE_DEVICE_BASE_URL`` 整体覆盖（值应含 /api/face/device，
    反代 / 容器端口映射 / 多网卡场景用，见 DEPLOY.md「.env 完整变量表」）；否则自动探测：
    UDP connect 到设备 IP 取本机路由出口 IP（connect 不发包，仅查路由表拿
    getsockname），端口取后端实际监听端口（run_backend.py 同源的 env PORT，
    默认 2124）。
    """
    override = os.environ.get("WAREHOUSE_DEVICE_BASE_URL")
    if override:
        return override.rstrip("/")
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            s.connect((device_ip, 9))
            local_ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        # 探测失败兜底（如设备 IP 不可路由）：回环。测试环境设备就是 127.0.0.1。
        local_ip = "127.0.0.1"
    port = int(os.environ.get("PORT", 2124))
    return f"http://{local_ip}:{port}/api/face/device"


def _ensure_tenant_auth_token(tid: int, current: "str | None") -> str:
    """租户级设备识别代理 Bearer（tenant_face_config.auth_token）为空时自动
    生成并持久化。条件更新防并发（仿 pull_token）：仅当仍为空时写入，写完
    无条件回读 DB 权威值。GET /api/face/config 会回传，UI「认证 Token」可见。"""
    token = (current or "").strip()
    if token:
        return token
    candidate = _secrets.token_hex(16)
    with get_engine().begin() as sa_conn:
        sa_conn.execute(
            update(_t_tenant_face_config)
            .where(and_(
                _t_tenant_face_config.c.tenant_id == tid,
                or_(_t_tenant_face_config.c.auth_token.is_(None),
                    _t_tenant_face_config.c.auth_token == ""),
            ))
            .values(auth_token=candidate)
        )
        token = sa_conn.execute(
            select(_t_tenant_face_config.c.auth_token)
            .where(_t_tenant_face_config.c.tenant_id == tid)
        ).scalar() or ""
    return token


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
    # 重新过一遍 IP 校验（SSRF 防线）：存量数据可能早于写入时校验。
    ip, _ = _validate_device_fields(dev.get("ip"), None)
    port = DEVICE_HTTP_PORT  # 固件写死 80，不读 device.port
    # 固定模型标签：全设备同模型，不读 mcp_agent_devices.model_tag。
    model_tag = DEVICE_FACE_MODEL_TAG

    # 取本租户人脸库并按固定 model_tag 过滤（复用 face 路由的 library 逻辑）。
    from routers.face import build_face_library
    tid = conn_row.tenant_id if hasattr(conn_row, "tenant_id") else dict(conn_row._mapping).get("tenant_id")

    # 下发前置懒重算（反方向）：lan 模式注册的 subject 只有远端模型（如 Hailo
    # 512D）的 enrollment + 注册照片，切回本机模式下发时设备需要 WE2 128D
    # embedding → 用进程内 WE2 模拟器从照片现算补行再下发。统一原则：enrollment
    # 按 (subject, model_tag) 多行共存，任何模型缺行且有照片就现算缓存，切换
    # 模式永不要求用户重录。失败 warn+跳过+进程内防重试集合（orchestrator 核心）。
    # push 本来就是用户主动的批量操作 → 不限量。任何异常不阻塞下发主链路。
    from face.orchestrator import ensure_enrollments_for_model
    from face import endpoint_client as _face_ec

    async def _local_infer(image_b64):
        return _face_ec._infer_local(image_b64)

    try:
        with get_db() as _lazy_conn:
            await ensure_enrollments_for_model(_lazy_conn, tid, model_tag, _local_infer)
    except Exception:
        import logging
        logging.getLogger("warehouse.face").exception(
            "push-faces lazy re-embed failed (non-fatal)")

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

    # 随人脸库一并下发设备侧识别配置（Web「配置与规则」是唯一事实源，改完
    # 重新下发即生效）：
    #   match_threshold  — 最低识别置信度（0-100），设备本地 Match 阈值
    #   identify_mode    — 'local' 设备 NPU + 本地库；'lan' 现场抓拍 POST 到
    #                      identify_endpoint 的 /recognize（face_rec_api 契约）
    # 旧固件忽略未知字段，向后兼容。
    with get_engine().connect() as sa_conn:
        fc = sa_conn.execute(
            select(
                _t_tenant_face_config.c.mode,
                _t_tenant_face_config.c.endpoint,
                _t_tenant_face_config.c.auth_token,
                _t_tenant_face_config.c.min_confidence,
            ).where(_t_tenant_face_config.c.tenant_id == tid)
        ).fetchone()
    if fc is not None:
        payload["match_threshold"] = int(round(float(fc.min_confidence or 0.65) * 100))
        identify_mode = fc.mode if fc.mode in ("local", "lan") else "local"
        payload["identify_mode"] = identify_mode
        if identify_mode == "lan":
            # 设备识别代理：lan 模式设备抓拍 POST 的是 **warehouse 自身**的
            # /api/face/device/recognize（伪装 face_rec_api /recognize 契约），
            # 不再直连租户端点——人脸库只在 warehouse 存一份，face_rec_api 保持
            # 无状态推理。identify_token 用租户级 auth_token（为空首发自动生成）。
            payload["identify_endpoint"] = _device_facing_base_url(ip)
            payload["identify_token"] = _ensure_tenant_auth_token(tid, fc.auth_token)
        else:
            # local 模式行为不变：设备本地 NPU + 本地库，endpoint/token 原样透传。
            payload["identify_endpoint"] = (fc.endpoint or "").strip()
            payload["identify_token"] = fc.auth_token or ""
        # faces 库两种模式都照常下发：固件注释明确「旧固件忽略未知字段」、lan 模式
        # 忽略本地库即可；保留下发让设备离线/回切 local 时仍有可用库，也避免
        # payload 结构按模式分叉（MAX_PUSH_FACES 上限检查逻辑保持单一路径）。

    # pull_token（B 方案后端直拉鉴权）：每设备独立，首次下发时生成并持久化，之后复用。
    # 与 identify_token 分离（后者仅 lan 模式有值），保证本机模式设备也有非空 token，
    # 否则设备端 current-speaker 的 fail-closed 会永远 401。下发到 NVS face.pull_token。
    pull_token = dev.get("pull_token")
    if not pull_token:
        # 条件更新：仅当仍为空时写入，避免两个并发 push 各生成一个 token 互相覆盖、
        # 导致设备 NVS 与 DB 永久不一致。写完无条件回读 DB 的权威值再下发。
        candidate = _secrets.token_hex(16)
        with get_engine().begin() as sa_conn:
            sa_conn.execute(
                update(_t_mcp_agent_devices)
                .where(and_(
                    _t_mcp_agent_devices.c.id == dev["id"],
                    or_(_t_mcp_agent_devices.c.pull_token.is_(None),
                        _t_mcp_agent_devices.c.pull_token == ""),
                ))
                .values(pull_token=candidate)
            )
            pull_token = sa_conn.execute(
                select(_t_mcp_agent_devices.c.pull_token)
                .where(_t_mcp_agent_devices.c.id == dev["id"])
            ).scalar()
    payload["pull_token"] = pull_token or ""

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
