"""Unit tests for build_scope_predicates() — Phase 2d."""
import os
import sys

# Ensure backend is on sys.path
_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend')
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import select, and_
from sqlalchemy.dialects import sqlite

from app import build_scope_predicates  # noqa: E402
from metadata import materials, contacts, users  # noqa: E402


def _compile(stmt):
    return str(stmt.compile(dialect=sqlite.dialect(),
                            compile_kwargs={"literal_binds": True}))


def test_no_tenant_no_warehouse_returns_empty():
    preds = build_scope_predicates(materials, None, None)
    assert preds == []


def test_tenant_only_adds_tenant_predicate():
    preds = build_scope_predicates(materials, 7, None)
    assert len(preds) == 1
    stmt = select(materials.c.id).where(and_(*preds))
    sql = _compile(stmt)
    assert "materials.tenant_id = 7" in sql
    assert "warehouse_id" not in sql


def test_tenant_and_warehouse_adds_both():
    preds = build_scope_predicates(materials, 7, 3)
    assert len(preds) == 2
    stmt = select(materials.c.id).where(and_(*preds))
    sql = _compile(stmt)
    assert "materials.tenant_id = 7" in sql
    assert "materials.warehouse_id = 3" in sql


def test_warehouse_only_global_admin():
    """tenant_id=None mirrors global admin: no tenant filter, but warehouse still scoped."""
    preds = build_scope_predicates(materials, None, 5)
    assert len(preds) == 1
    stmt = select(materials.c.id).where(and_(*preds))
    sql = _compile(stmt)
    assert "tenant_id" not in sql
    assert "materials.warehouse_id = 5" in sql


def test_works_on_other_tables():
    """Function must accept any Table with tenant_id/warehouse_id columns."""
    # contacts has both tenant_id and warehouse_id columns
    preds = build_scope_predicates(contacts, 2, 4)
    assert len(preds) == 2
    sql = _compile(select(contacts.c.id).where(and_(*preds)))
    assert "contacts.tenant_id = 2" in sql
    assert "contacts.warehouse_id = 4" in sql


def test_users_table_tenant_only():
    """users table has tenant_id but no warehouse_id — caller passes warehouse_id=None."""
    preds = build_scope_predicates(users, 9, None)
    assert len(preds) == 1
    sql = _compile(select(users.c.id).where(and_(*preds)))
    assert "users.tenant_id = 9" in sql
