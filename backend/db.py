"""SQLAlchemy engine factory.

Phase 1 of the SQLite -> SQLAlchemy + Alembic migration. This module is
intentionally a parallel access path to ``backend.database.get_db_connection``
(raw sqlite3). Existing code keeps using the raw sqlite3 connection; new code
added in later phases will move to the Engine here.

Driver selection is purely env-driven via ``DATABASE_URL``:

* ``sqlite:///<path>``   - local dev, default behaviour
* ``mysql+pymysql://...`` - cloud MySQL 8 (utf8mb4)

Defaults to ``sqlite:///<DATABASE_PATH or 'warehouse.db'>``.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool, QueuePool


_engine: Engine | None = None
_engine_url: str | None = None


def _resolve_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Match backend.database.DATABASE_PATH default.
    db_path = os.environ.get("DATABASE_PATH", "warehouse.db")
    return f"sqlite:///{db_path}"


def _build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        eng = create_engine(
            url,
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA foreign_keys=ON")
            finally:
                cur.close()

        return eng

    if url.startswith("mysql"):
        # Ensure utf8mb4 is set on the connection regardless of URL params.
        connect_args = {"charset": "utf8mb4"}
        eng = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=5,
            pool_pre_ping=True,
            connect_args=connect_args,
            future=True,
            # AUTOCOMMIT prevents pooled connections from accumulating
            # implicit transactions across reads. Writes still use
            # explicit `engine.begin()` blocks, which override this and
            # wrap the body in BEGIN/COMMIT.
            isolation_level="AUTOCOMMIT",
        )
        return eng

    # Unknown dialect — let SQLAlchemy decide; no pool tuning.
    return create_engine(url, future=True)


def get_engine() -> Engine:
    """Return the process-wide engine, creating it lazily.

    Re-resolves DATABASE_URL on each call; if it has changed since the
    cached engine was built (e.g. a test fixture swapped DATABASE_PATH),
    the stale engine is disposed and a fresh one is built. This makes
    the module safe to use in test suites that monkeypatch the env.
    """
    global _engine, _engine_url
    url = _resolve_database_url()
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _engine.dispose()
        _engine = _build_engine(url)
        _engine_url = url
    return _engine


def reset_engine() -> None:
    """Force-dispose the cached engine. Useful for tests."""
    global _engine, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None


# Convenience alias so callers can ``from backend.db import engine``.
def __getattr__(name: str):  # pragma: no cover - import-time helper
    if name == "engine":
        return get_engine()
    raise AttributeError(name)


@contextmanager
def get_connection() -> Iterator:
    """Context manager yielding a SQLAlchemy Connection.

    Wraps in a transaction; commits on success, rolls back on exception.
    """
    eng = get_engine()
    with eng.connect() as conn:
        with conn.begin():
            yield conn
