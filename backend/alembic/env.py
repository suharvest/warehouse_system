"""Alembic environment for warehouse_system.

Reads ``DATABASE_URL`` from the process environment, falling back to a local
SQLite file. Imports ``target_metadata`` from ``backend.metadata`` so
autogenerate stays in sync with the SQLAlchemy Core schema.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# Make the repo root importable so ``backend.metadata`` resolves regardless of
# whether alembic is invoked from ``backend/`` or the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.metadata import target_metadata  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    cfg_url = config.get_main_option("sqlalchemy.url")
    if cfg_url and not cfg_url.startswith("driver://"):
        return cfg_url
    db_path = os.environ.get("DATABASE_PATH", "warehouse.db")
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _resolve_url()
    connectable = create_engine(url, poolclass=pool.NullPool, future=True)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=url.startswith("sqlite"),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
