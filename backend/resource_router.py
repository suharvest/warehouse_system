"""
ResourceRouter — small CRUD-route factory for resource families.

Currently migrated: contacts, warehouses, users, api-keys, ERP providers
(GET/PUT/DELETE only — POST is multipart upload, hand-rolled).

Intentionally hand-rolled (factory cost > savings):
  * MCP connections — async lifecycle (start/stop/restart) interleaved
    with create/update/delete; response wrapper ``MCPConnectionResponse``
    differs from the row shape; cascading api_keys cleanup on DELETE.
    Goldens still in place at ``tests/contracts/mcp/``.
  * ERP POST  — multipart file upload (``UploadFile = File(...)``).
  * Face rules / subjects / enrollments — tenant resolved via *query
    parameter* (``_face_resolve_tenant``), which the factory's hook
    contract does not surface to per-verb closures. Goldens at
    ``tests/contracts/face_*/``.

Design goals
------------
- Additive. Does not change URL paths, HTTP methods, status codes, response
  shapes, or permission dependencies. Contract tests in
  ``tests/test_resource_router_contract.py`` lock the wire format.
- Hook-driven, not magic. Resource-specific logic (validation, side effects,
  tenant resolution, fuzzy_matcher invalidation) is wired in via callables;
  the factory contributes only the boilerplate (route registration, common
  ``load_or_404`` lookup, transaction scope).
- Minimal surface. Only the hooks contacts needs are present; future
  migrations may add more, but YAGNI.

Hook contract (all optional unless marked required)
---------------------------------------------------
* ``list_handler``  — REQUIRED if a list endpoint is wanted; receives the
  raw FastAPI dependencies and returns the response. Contacts has filters
  + pagination shape, so it supplies its own.
* ``to_out``       — REQUIRED. Converts a fetched SA Row into the Pydantic
  response model used by GET / UPDATE.
* ``before_create``, ``before_update``, ``before_delete`` — receive
  ``(sa_conn, current_user, request)`` (and ``row`` for update/delete).
  May raise HTTPException; may return a dict of values that override /
  augment the auto-derived insert/update payload.
* ``values_for_create`` — REQUIRED for CREATE. Given
  ``(sa_conn, current_user, request)`` returns a ``dict`` of column =>
  value to insert. Tenant resolution lives here (contacts needs the
  global-admin-must-pass-tenant_id dance).
* ``values_for_update`` — REQUIRED for UPDATE. Given the validated
  request returns a ``dict`` of column => value to update. Empty dict
  is allowed (no-op update).
* ``after_commit`` — receives ``(operation, sa_conn, current_user,
  row_id)`` AFTER a successful CREATE / UPDATE / DELETE commit (still
  inside the ``begin()`` block, so anything you do here is part of the
  same transaction). Used for fuzzy_matcher invalidation.
* ``delete_response`` — dict returned from DELETE. Defaults to
  ``{"success": True}`` but resources can pass a custom message.

Notes on shape parity
---------------------
The factory does NOT decide what GET / UPDATE return. It calls ``to_out``
and returns whatever that produces (typically a Pydantic model). Same for
LIST: it just calls your ``list_handler``. This keeps response shapes
locked to the existing handlers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type, Union

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import Table, and_, insert, select, update as sa_update


# Imported lazily inside register() so we don't create a hard cycle with
# the registration target (FastAPI app or APIRouter). The functions are stable.


def _load_helpers():
    # Local import avoids circular import at module load time. Pull helpers
    # straight from deps / db rather than re-exporting through app.py — this
    # keeps the dependency arrow pointing away from app.py so we can register
    # ResourceRouter routes onto APIRouter instances declared in router modules.
    from deps import load_or_404  # type: ignore
    from db import get_engine  # type: ignore
    return load_or_404, get_engine


@dataclass
class ResourceRouter:
    """Factory for CRUD routes on a single SA Core table.

    Parameters
    ----------
    app : FastAPI
        The application to register routes on.
    prefix : str
        URL prefix without trailing slash, e.g. ``"/api/contacts"``.
    table : sqlalchemy.Table
        The SA Core table the resource maps to.
    response_model : Type[BaseModel]
        Pydantic model for GET / UPDATE responses.
    create_model : Type[BaseModel]
        Pydantic body model for POST.
    update_model : Type[BaseModel]
        Pydantic body model for PUT.
    permission_read, permission_write : Depends
        FastAPI ``Depends(...)`` objects for read / write gates. The
        caller passes these so the factory does NOT bypass
        ``require_permission``.
    not_found_detail, forbidden_detail : str
        Detail strings forwarded to ``load_or_404`` (and re-used by
        UPDATE / DELETE). Wire format is preserved by the existing
        ``HTTPException`` handler at ``backend/app.py:271``.
    to_out : callable(row) -> BaseModel | dict
        Maps an SA Row to the response payload.
    values_for_create : callable(sa_conn, current_user, request) -> dict
    values_for_update : callable(sa_conn, current_user, request, row) -> dict
    list_handler : callable | None
        Full FastAPI handler for ``GET /<prefix>``. Receives the raw
        request dependencies; returns the response object. None disables
        the LIST route.
    get_columns : list[Column] | None
        Columns to select on GET. None selects all columns (``select(table)``).
    update_select_columns : list[Column] | None
        Columns to re-select after UPDATE so we can return a fresh row.
        None selects all columns.
    before_create, before_update, before_delete, after_commit, delete_response :
        See module docstring.
    """

    app: Union[FastAPI, APIRouter]
    prefix: str
    table: Table
    # ``None`` skips ``response_model=`` on the FastAPI route declarations,
    # used by resources whose GET payload is a dict-of-Any (e.g. ERP
    # providers) rather than a Pydantic model.
    response_model: Optional[Type[BaseModel]]
    create_model: Type[BaseModel]
    update_model: Type[BaseModel]
    permission_read: Any
    permission_write: Any
    not_found_detail: str
    forbidden_detail: str

    to_out: Callable[[Any], Any]
    values_for_create: Callable[..., Dict[str, Any]]
    values_for_update: Callable[..., Dict[str, Any]]

    list_handler: Optional[Callable[..., Any]] = None
    get_columns: Optional[List[Any]] = None
    update_select_columns: Optional[List[Any]] = None
    # Columns used by ``load_or_404`` inside the PUT and DELETE handlers
    # before the resource-specific ``before_update`` / ``before_delete`` /
    # ``values_for_update`` hooks run. Defaults to ``[table.c.id,
    # table.c.tenant_id]`` (the minimum needed for the scoped-row check).
    # Resources whose hooks need additional columns (e.g. warehouses' hooks
    # read ``is_default`` to forbid disabling/deleting the default warehouse)
    # override this so the row is loaded atomically with its scope check —
    # avoiding a second SELECT vs. a row that may have been mutated in
    # between.
    load_columns: Optional[List[Any]] = None
    id_path_name: str = "item_id"

    before_create: Optional[Callable[..., None]] = None
    before_update: Optional[Callable[..., None]] = None
    before_delete: Optional[Callable[..., None]] = None
    after_commit: Optional[Callable[..., None]] = None
    delete_response: Dict[str, Any] = field(
        default_factory=lambda: {"success": True}
    )
    # Optional override for the CREATE response shape.
    # Signature: ``(row, *, request, sa_conn, current_user) -> Any``.
    # Falls back to ``to_out(row)`` when None. Used for resources whose
    # POST response carries one-shot payload (plaintext secrets, etc.) that
    # later GET/UPDATE responses do NOT include — e.g. api-keys returns the
    # plaintext key once on create only.
    to_out_create: Optional[Callable[..., Any]] = None
    # Optional override for the UPDATE response shape.
    # Signature: ``(row, *, request, item_id, sa_conn, current_user) -> Any``.
    # Falls back to ``to_out(row)`` when None. Used for resources whose
    # PUT response is a status envelope (``{"success": True}``) instead of
    # the refreshed row — e.g. ERP providers and face/rules.
    to_out_update: Optional[Callable[..., Any]] = None
    # Hard-delete switch — when True, DELETE issues a SQL ``DELETE`` instead
    # of the default ``UPDATE ... SET is_disabled=1`` soft-delete. Resources
    # without ``is_disabled`` (or where hard-delete is the historical wire
    # behaviour) need this. Ignored if ``before_delete`` is supplied (the
    # caller is expected to perform whatever delete semantic they want).
    hard_delete: bool = False
    # Per-verb toggles. Useful when the existing public API does not expose
    # one of GET/POST/PUT/DELETE on the prefix (e.g. api-keys never had a
    # GET-by-id or PUT route — only POST + DELETE + a side-route
    # /{id}/status). Defaults preserve the prior contacts behaviour.
    enable_get: bool = True
    enable_post: bool = True
    enable_put: bool = True
    enable_delete: bool = True

    def register(self) -> None:
        """Wire all configured routes onto ``self.app``."""
        load_or_404, get_engine = _load_helpers()

        prefix = self.prefix

        # --- LIST -----------------------------------------------------------
        if self.list_handler is not None:
            self.app.get(prefix)(self.list_handler)

        # --- GET / POST / PUT / DELETE -------------------------------------
        if self.enable_get:
            self._register_get(get_engine, load_or_404)
        if self.enable_post:
            self._register_post(get_engine)
        if self.enable_put:
            self._register_put(get_engine, load_or_404)
        if self.enable_delete:
            self._register_delete(get_engine, load_or_404)

    # ------------------------------------------------------------------
    # Per-verb registration. We build closures with explicit signatures so
    # FastAPI's dependency-injection (``Depends``) and path-parameter
    # binding work without inspecting **kwargs.
    # ------------------------------------------------------------------

    def _register_get(self, get_engine, load_or_404):
        prefix = self.prefix
        table = self.table
        permission_read = self.permission_read
        get_columns = self.get_columns
        not_found = self.not_found_detail
        forbidden = self.forbidden_detail
        to_out = self.to_out

        get_kwargs: Dict[str, Any] = {}
        if self.response_model is not None:
            get_kwargs["response_model"] = self.response_model

        @self.app.get(f"{prefix}/{{item_id}}", **get_kwargs)
        async def get_item(item_id: int, current_user=Depends(permission_read)):
            with get_engine().connect() as sa_conn:
                row = load_or_404(
                    sa_conn,
                    table,
                    item_id,
                    columns=get_columns,
                    not_found=not_found,
                    tenant_id=current_user.tenant_id,
                    forbidden=forbidden,
                )
            return to_out(row)

        get_item.__name__ = f"{table.name}_get"
        return get_item

    def _register_post(self, get_engine):
        prefix = self.prefix
        table = self.table
        permission_write = self.permission_write
        create_model = self.create_model
        before_create = self.before_create
        values_for_create = self.values_for_create
        after_commit = self.after_commit
        update_select_columns = self.update_select_columns
        to_out = self.to_out
        to_out_create = self.to_out_create
        response_model = self.response_model

        async def create_item(request, current_user=Depends(permission_write)):
            with get_engine().begin() as sa_conn:
                if before_create is not None:
                    before_create(sa_conn, current_user, request)
                values = values_for_create(sa_conn, current_user, request)
                result = sa_conn.execute(insert(table).values(**values))
                new_id = result.inserted_primary_key[0]
                if after_commit is not None:
                    after_commit("create", sa_conn, current_user, new_id)

                # Re-select to return the canonical row (matches existing
                # contacts.create behaviour, which returns inserted values
                # plus DB-side defaults like is_disabled=False).
                cols = update_select_columns
                stmt = (
                    select(*cols).where(table.c.id == new_id)
                    if cols is not None
                    else select(table).where(table.c.id == new_id)
                )
                fresh = sa_conn.execute(stmt).first()
            if to_out_create is not None:
                return to_out_create(
                    fresh, request=request,
                    sa_conn=sa_conn, current_user=current_user,
                )
            return to_out(fresh)

        # Set the body-param annotation programmatically so FastAPI sees a
        # real Pydantic class, not a ForwardRef of a closure-local name.
        create_item.__annotations__["request"] = create_model
        create_item.__name__ = f"{table.name}_create"
        post_kwargs: Dict[str, Any] = {}
        if self.response_model is not None:
            post_kwargs["response_model"] = self.response_model
        self.app.post(prefix, **post_kwargs)(create_item)
        return create_item

    def _register_put(self, get_engine, load_or_404):
        prefix = self.prefix
        table = self.table
        permission_write = self.permission_write
        update_model = self.update_model
        before_update = self.before_update
        values_for_update = self.values_for_update
        after_commit = self.after_commit
        get_columns = self.get_columns
        update_select_columns = self.update_select_columns
        not_found = self.not_found_detail
        forbidden = self.forbidden_detail
        to_out = self.to_out

        load_columns = self.load_columns
        to_out_update = self.to_out_update

        async def update_item(item_id: int, request=None, current_user=Depends(permission_write)):
            with get_engine().begin() as sa_conn:
                row = load_or_404(
                    sa_conn,
                    table,
                    item_id,
                    columns=load_columns if load_columns is not None
                            else [table.c.id, table.c.tenant_id],
                    not_found=not_found,
                    tenant_id=current_user.tenant_id,
                    forbidden=forbidden,
                )
                if before_update is not None:
                    before_update(sa_conn, current_user, request, row)
                values = values_for_update(sa_conn, current_user, request, row)
                if values:
                    # 防御性：WHERE 加 tenant_id 兜底（load_or_404 已检过一次，
                    # 但 update 时若行被并发 reparent 或 load 路径有 bug，这层
                    # 是最后一道防线）。全局 admin（tenant_id is None）跳过此
                    # 约束允许跨租户管理。
                    where_clauses = [table.c.id == item_id]
                    if current_user.tenant_id is not None:
                        where_clauses.append(table.c.tenant_id == current_user.tenant_id)
                    res = sa_conn.execute(
                        sa_update(table).where(and_(*where_clauses)).values(**values)
                    )
                    if res.rowcount != 1:
                        raise HTTPException(status_code=403, detail=forbidden)
                    if after_commit is not None:
                        after_commit("update", sa_conn, current_user, item_id)

                cols = update_select_columns
                stmt = (
                    select(*cols).where(table.c.id == item_id)
                    if cols is not None
                    else select(table).where(table.c.id == item_id)
                )
                fresh = sa_conn.execute(stmt).first()
            if to_out_update is not None:
                return to_out_update(
                    fresh, request=request, item_id=item_id,
                    sa_conn=sa_conn, current_user=current_user,
                )
            return to_out(fresh)

        update_item.__annotations__["request"] = update_model
        update_item.__name__ = f"{table.name}_update"
        # When ``to_out_update`` is set the wire shape is no longer the
        # response_model — drop it so FastAPI doesn't try to validate the
        # status envelope against the resource Pydantic class.
        put_kwargs: Dict[str, Any] = {}
        if to_out_update is None and self.response_model is not None:
            put_kwargs["response_model"] = self.response_model
        self.app.put(f"{prefix}/{{item_id}}", **put_kwargs)(update_item)
        return update_item

    def _register_delete(self, get_engine, load_or_404):
        prefix = self.prefix
        table = self.table
        permission_write = self.permission_write
        before_delete = self.before_delete
        after_commit = self.after_commit
        not_found = self.not_found_detail
        forbidden = self.forbidden_detail
        delete_response = self.delete_response
        hard_delete = self.hard_delete
        load_columns = self.load_columns

        @self.app.delete(f"{prefix}/{{item_id}}")
        async def delete_item(
            item_id: int,
            current_user=Depends(permission_write),
        ):
            with get_engine().begin() as sa_conn:
                row = load_or_404(
                    sa_conn,
                    table,
                    item_id,
                    columns=load_columns if load_columns is not None
                            else [table.c.id, table.c.tenant_id],
                    not_found=not_found,
                    tenant_id=current_user.tenant_id,
                    forbidden=forbidden,
                )
                # 同 update：WHERE 加 tenant_id 防御越租户写入。before_delete
                # 由资源自己拼 SQL，需要资源自己保证 tenant scope；这里只兜底
                # 默认 hard/soft delete 路径。
                where_clauses = [table.c.id == item_id]
                if current_user.tenant_id is not None:
                    where_clauses.append(table.c.tenant_id == current_user.tenant_id)
                if before_delete is not None:
                    before_delete(sa_conn, current_user, row)
                elif hard_delete:
                    # Hard delete — used by resources whose historical
                    # wire behaviour was a SQL DELETE (api-keys).
                    from sqlalchemy import delete as sa_delete
                    res = sa_conn.execute(sa_delete(table).where(and_(*where_clauses)))
                    if res.rowcount != 1:
                        raise HTTPException(status_code=403, detail=forbidden)
                else:
                    # Default: soft-disable via ``is_disabled``. Resources
                    # without that column MUST supply a before_delete that
                    # performs whatever delete semantic they want (hard
                    # delete, cascade, etc.).
                    res = sa_conn.execute(
                        sa_update(table)
                        .where(and_(*where_clauses))
                        .values(is_disabled=1)
                    )
                    if res.rowcount != 1:
                        raise HTTPException(status_code=403, detail=forbidden)
                if after_commit is not None:
                    after_commit("delete", sa_conn, current_user, item_id)
            return delete_response

        delete_item.__name__ = f"{table.name}_delete"
        return delete_item
