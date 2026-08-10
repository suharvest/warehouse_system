#!/usr/bin/env python3
"""Thin CLI wrapper around ``backend.legacy_db_migration``.

The migration logic lives in ``backend/legacy_db_migration.py`` so that it is
importable from the app at startup *and* from the production image (which only
ships ``backend/``). This wrapper exists purely so the historical host command
keeps working:

    uv run python scripts/migrate_legacy_db.py /path/to/warehouse.db

Inside the container, ``scripts/`` is not present — use the module entry point
instead:

    /app/.venv/bin/python -m backend.legacy_db_migration /data/warehouse.db
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.legacy_db_migration import main  # noqa: E402

if __name__ == "__main__":
    main()
