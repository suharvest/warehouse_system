"""Shared FastAPI dependencies extracted from ``app.py``.

Phase 1 of the ``app.py`` split (task #5). This module owns the cross-cutting
primitives that any router needs:

  * ``get_db``  — sqlite-compatible connection context manager
  * ``Role`` / ``Resource`` / ``Action`` — permission enums
  * ``CurrentUser`` — request-scoped user descriptor
  * ``get_current_user`` — auth dependency
  * ``require_permission`` — permission dependency factory
  * ``load_or_404`` — common 404/403 helper

These were defined in ``app.py`` before; the bodies below are copied
verbatim (no logic changes) so that the snapshot in
``tests/fixtures/route_inventory.json`` remains byte-for-byte identical.
"""
from contextlib import contextmanager
from datetime import datetime
from enum import Enum, IntEnum
from typing import List, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import and_, or_, select, update

from database import (
    get_db_connection,
    hash_api_key,
    get_deploy_mode,
)
from db import get_engine
from metadata import (
    api_keys as _t_api_keys,
    sessions as _t_sessions,
    tenants as _t_tenants,
    user_warehouses as _t_user_warehouses,
    users as _t_users,
    warehouses as _t_warehouses,
)
from models import RoleName


# ============ DB connection context manager ============

@contextmanager
def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


# ============ Role level mapping ============

# 权限级别映射（数字越大权限越高）。RoleName members are str subclasses
# so dict lookups keyed by RoleName also accept their raw ``.value`` —
# RoleName.ADMIN look up the same level.
ROLE_LEVELS = {
    RoleName.VIEW: 1,
    RoleName.OPERATE: 2,
    RoleName.ADMIN: 3,
}


class Role(IntEnum):
    """Numeric role *level* ordering. Higher = more privileged.

    This is distinct from ``RoleName`` (the wire-format string enum). Use
    ``Role`` only for comparisons inside ``require_permission``.
    """
    VIEW = 1
    OPERATE = 2
    ADMIN = 3

    @classmethod
    def from_str(cls, s: str) -> "Role":
        # ``s`` may be a raw string or a ``RoleName`` member (which is also
        # a str). ``.lower()`` works for both.
        return {"view": cls.VIEW, "operate": cls.OPERATE, "admin": cls.ADMIN}[s.lower()]


class Resource(str, Enum):
    """Resource categories used by require_permission()."""
    CONTACTS = "contacts"
    USERS = "users"
    MATERIALS = "materials"
    INVENTORY = "inventory"
    WAREHOUSES = "warehouses"
    TENANTS = "tenants"
    API_KEYS = "api_keys"
    MCP = "mcp"
    ERP = "erp"
    FACE = "face"
    SYSTEM = "system"
    DASHBOARD = "dashboard"
    SEARCH = "search"
    AUTH = "auth"


class Action(str, Enum):
    """Actions that can be performed on a Resource."""
    READ = "read"        # → Role.VIEW
    WRITE = "write"      # → Role.OPERATE
    ADMIN = "admin"      # → Role.ADMIN


_ACTION_TO_ROLE: "dict[Action, Role]" = {
    Action.READ: Role.VIEW,
    Action.WRITE: Role.OPERATE,
    Action.ADMIN: Role.ADMIN,
}


class CurrentUser:
    """当前用户信息"""
    def __init__(self, user_id: int = None, username: str = None,
                 display_name: str = None, role: str = RoleName.VIEW.value,
                 is_guest: bool = True, source: str = 'guest',
                 warehouse_id: int = None,
                 tenant_id: int = 1):
        self.id = user_id
        self.username = username
        self.display_name = display_name
        self.role = role
        self.is_guest = is_guest
        self.source = source  # 'session' | 'api_key' | 'guest'
        self.warehouse_id = warehouse_id  # 从API key自动绑定的仓库
        self.tenant_id = tenant_id  # 所属租户ID

    def has_permission(self, min_role: str) -> bool:
        """检查是否有最低权限"""
        return ROLE_LEVELS.get(self.role, 0) >= ROLE_LEVELS.get(min_role, 0)

    def get_operator_name(self) -> str:
        """获取操作人名称"""
        if self.display_name:
            return self.display_name
        if self.username:
            return self.username
        return "访客"

    def get_authorized_warehouses(self, conn) -> List[int]:
        """获取用户授权的仓库ID列表。全局 admin 可访问所有仓库，租户 admin 仅本租户。

        Phase 2b: read via SQLAlchemy Core. ``conn`` retained for signature
        compatibility but unused.
        """
        with get_engine().connect() as sa_conn:
            if self.role == RoleName.ADMIN:
                if self.tenant_id is None:
                    stmt = select(_t_warehouses.c.id).where(_t_warehouses.c.is_disabled == 0)
                else:
                    stmt = select(_t_warehouses.c.id).where(
                        and_(
                            _t_warehouses.c.tenant_id == self.tenant_id,
                            _t_warehouses.c.is_disabled == 0,
                        )
                    )
                return [r.id for r in sa_conn.execute(stmt).fetchall()]
            stmt = select(_t_user_warehouses.c.warehouse_id).where(
                _t_user_warehouses.c.user_id == self.id
            )
            return [r.warehouse_id for r in sa_conn.execute(stmt).fetchall()]

    def can_access_warehouse(self, conn, warehouse_id: int) -> bool:
        """检查用户是否有权访问指定仓库。全局 admin 可访问任意仓库，租户 admin 仅本租户。

        Phase 2b: read via SQLAlchemy Core. ``conn`` retained for signature
        compatibility but unused.
        """
        if self.role == RoleName.ADMIN:
            if self.tenant_id is None:
                return True
            stmt = select(_t_warehouses.c.id).where(
                and_(
                    _t_warehouses.c.id == warehouse_id,
                    _t_warehouses.c.tenant_id == self.tenant_id,
                )
            ).limit(1)
            with get_engine().connect() as sa_conn:
                return sa_conn.execute(stmt).first() is not None
        # API key 携带仓库绑定即作为授权依据（MCP/Agent 场景）
        if self.source == 'api_key' and self.warehouse_id is not None:
            return self.warehouse_id == warehouse_id
        stmt = select(_t_user_warehouses.c.id).where(
            and_(
                _t_user_warehouses.c.user_id == self.id,
                _t_user_warehouses.c.warehouse_id == warehouse_id,
            )
        ).limit(1)
        with get_engine().connect() as sa_conn:
            return sa_conn.execute(stmt).first() is not None


async def get_current_user(request: Request) -> CurrentUser:
    """
    获取当前用户（认证中间件）
    优先级：X-API-Key > session_token Cookie > 访客
    """
    # 1. 检查 X-API-Key Header — Phase 3e: SA Core single short txn
    api_key = request.headers.get('X-API-Key')
    if api_key:
        key_hash = hash_api_key(api_key)
        ak_select = select(
            _t_api_keys.c.id,
            _t_api_keys.c.name,
            _t_api_keys.c.role,
            _t_api_keys.c.user_id,
            _t_api_keys.c.warehouse_id,
            _t_api_keys.c.tenant_id,
            _t_users.c.username,
            _t_users.c.display_name,
        ).select_from(
            _t_api_keys.outerjoin(_t_users, _t_api_keys.c.user_id == _t_users.c.id)
                       .outerjoin(_t_tenants, _t_api_keys.c.tenant_id == _t_tenants.c.id)
        ).where(
            and_(
                _t_api_keys.c.key_hash == key_hash,
                _t_api_keys.c.is_disabled == 0,
                or_(_t_api_keys.c.tenant_id.is_(None), _t_tenants.c.is_active == 1),
            )
        )
        with get_engine().begin() as sa_conn:
            key_row = sa_conn.execute(ak_select).first()
            if key_row:
                sa_conn.execute(
                    update(_t_api_keys)
                    .where(_t_api_keys.c.id == key_row.id)
                    .values(last_used_at=datetime.now())
                )

        if key_row:
            display_name = key_row.display_name or key_row.username or key_row.name
            return CurrentUser(
                user_id=key_row.user_id,
                username=key_row.username or key_row.name,
                display_name=display_name,
                role=key_row.role,
                is_guest=False,
                source='api_key',
                warehouse_id=key_row.warehouse_id,
                tenant_id=key_row.tenant_id
            )

    # 2. 检查 session_token Cookie
    # Phase 2b: read via SQLAlchemy Core (pure SELECT).
    session_token = request.cookies.get('session_token')
    if session_token:
        stmt = select(
            _t_sessions.c.user_id,
            _t_sessions.c.expires_at,
            _t_users.c.username,
            _t_users.c.display_name,
            _t_users.c.role,
            _t_users.c.tenant_id,
        ).select_from(
            _t_sessions.join(_t_users, _t_sessions.c.user_id == _t_users.c.id)
                       .outerjoin(_t_tenants, _t_users.c.tenant_id == _t_tenants.c.id)
        ).where(
            and_(
                _t_sessions.c.token == session_token,
                _t_users.c.is_disabled == 0,
                _t_sessions.c.revoked_at.is_(None),
                or_(_t_users.c.tenant_id.is_(None), _t_tenants.c.is_active == 1),
            )
        )
        with get_engine().connect() as sa_conn:
            session_row = sa_conn.execute(stmt).first()

        if session_row:
            # 检查是否过期 — SA returns DateTime as datetime or string depending on dialect
            ea = session_row.expires_at
            if isinstance(ea, datetime):
                expires_at = ea
            else:
                expires_at = datetime.strptime(str(ea), '%Y-%m-%d %H:%M:%S')
            if expires_at > datetime.now():
                return CurrentUser(
                    user_id=session_row.user_id,
                    username=session_row.username,
                    display_name=session_row.display_name,
                    role=session_row.role,
                    is_guest=False,
                    source='session',
                    tenant_id=session_row.tenant_id if session_row.tenant_id is not None else None
                )

    # 3. 访客模式
    if get_deploy_mode() == 'multi_tenant':
        return CurrentUser(tenant_id=None)  # 多租户下访客无 tenant_id
    return CurrentUser(tenant_id=1)


def require_permission(resource: Resource, action: Action):
    """Dependency factory keyed by (resource, action).

    Produces byte-for-byte identical 401/403 responses to ``require_auth``
    (same Chinese error strings, same status codes), so existing tests
    continue to pass when an endpoint is migrated.

    The returned dependency is tagged with ``__perm_marker__`` so the
    boot-time route audit can recognise routes that use the new machinery.
    """
    if action not in _ACTION_TO_ROLE:
        raise ValueError(f"Unknown action: {action}")
    min_role = _ACTION_TO_ROLE[action]

    async def _dep(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        # Match require_auth() exactly: reject guests with 401 first.
        if current_user.is_guest:
            raise HTTPException(status_code=401, detail="请先登录")
        try:
            user_role = Role.from_str(current_user.role)
        except (KeyError, AttributeError):
            raise HTTPException(status_code=403, detail="权限不足")
        if user_role < min_role:
            raise HTTPException(status_code=403, detail="权限不足")
        return current_user

    _dep.__perm_marker__ = True
    _dep.__resource__ = resource
    _dep.__action__ = action
    return _dep


# ============ R1: load_or_404 helper ============

def load_or_404(
    sa_conn,
    table,
    id_value,
    *,
    not_found: str,
    id_column=None,
    columns=None,
    tenant_id=None,
    tenant_column=None,
    forbidden: Optional[str] = None,
):
    """Fetch a single row by id with consistent 404 / 403 semantics.

    Centralizes the repeated pattern across CRUD routes:

        row = sa_conn.execute(select(...).where(id == X)).first()
        if not row: raise HTTPException(404, "...不存在")
        if current_user.tenant_id is not None and row.tenant_id != current_user.tenant_id:
            raise HTTPException(403, "...")

    Parameters
    ----------
    sa_conn : sqlalchemy.engine.Connection
        An already-open connection (from ``get_engine().connect()`` or
        ``.begin()``). The helper does not manage connection lifecycle.
    table : sqlalchemy.Table
        The table to query.
    id_value :
        The id value to match.
    not_found : str
        Detail string for the 404 ``HTTPException``.
    id_column : sqlalchemy.Column, optional
        Column to match ``id_value`` against. Defaults to ``table.c.id``.
    columns : list[Column], optional
        Specific columns to select. When ``None`` the whole row is selected
        (``select(table)``), preserving the all-columns behaviour of the
        original sites.
    tenant_id :
        When not ``None``, the returned row's ``tenant_column`` value must
        equal this. Pass the caller's ``current_user.tenant_id`` (or a
        resolved ``tid``) — when the caller is a global admin with no
        tenant scope, pass ``None`` to skip the check.
    tenant_column : sqlalchemy.Column, optional
        Column used for the tenant comparison. Defaults to
        ``table.c.tenant_id`` when ``tenant_id`` is provided.
    forbidden : str, optional
        Detail string for the 403 ``HTTPException``. Required when
        ``tenant_id`` is provided.

    Returns
    -------
    sqlalchemy Row
        The fetched row (whatever was selected).

    Raises
    ------
    HTTPException
        404 if no row matches; 403 if the tenant check fails.
    """
    if id_column is None:
        id_column = table.c.id
    if columns is None:
        stmt = select(table).where(id_column == id_value)
    else:
        stmt = select(*columns).where(id_column == id_value)
    row = sa_conn.execute(stmt).first()
    if not row:
        raise HTTPException(status_code=404, detail=not_found)
    if tenant_id is not None:
        if forbidden is None:
            raise RuntimeError("load_or_404: 'forbidden' is required when tenant_id is provided")
        col = tenant_column if tenant_column is not None else table.c.tenant_id
        # Resolve the tenant value from the row by column name. Works for
        # both Row (attribute) and dict-mapping access.
        col_name = col.name if hasattr(col, 'name') else str(col)
        try:
            row_tenant = getattr(row, col_name)
        except AttributeError:
            row_tenant = row._mapping.get(col_name)
        if row_tenant != tenant_id:
            raise HTTPException(status_code=403, detail=forbidden)
    return row
