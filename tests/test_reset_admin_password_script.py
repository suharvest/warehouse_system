"""Tests for ``scripts/reset_admin_password.py`` (ops tool run on the host).

The script is exercised as a subprocess against a throwaway SQLite DB built
with ``alembic upgrade head``, so it sees the same schema a deployment has and
its engine / env handling is not shared with the test process.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
SCRIPT = REPO_ROOT / "scripts" / "reset_admin_password.py"
sys.path.insert(0, str(BACKEND_DIR))

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

NEW_PW = "NewPass123!"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hashes(path: Path) -> dict[int, str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT id, password_hash FROM users"))
    finally:
        conn.close()


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "warehouse.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(path))
    try:
        existing = {r[0] for r in conn.execute("SELECT id FROM tenants")}
        for tid, slug in ((1, "t1"), (2, "t2")):
            if tid not in existing:
                conn.execute(
                    "INSERT INTO tenants (id, slug, name) VALUES (?, ?, ?)",
                    (tid, slug, f"租户{tid}"),
                )
        conn.executemany(
            "INSERT INTO users (id, username, password_hash, role, tenant_id, is_disabled) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            [
                (101, "admin", "old-hash-1", "admin", 1),
                (102, "admin", "old-hash-2", "admin", 2),
                (103, "ops", "old-hash-3", "operate", 1),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _run(path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{path}"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def test_list_does_not_modify_db(db_path):
    before = _digest(db_path)
    r = _run(db_path, "--list")
    assert r.returncode == 0, r.stderr
    assert "admin" in r.stdout and "ops" in r.stdout
    assert _digest(db_path) == before


def test_ambiguous_username_without_tenant_id_exits_untouched(db_path):
    before = _hashes(db_path)
    r = _run(db_path, "admin", "--password", NEW_PW, "--yes")
    assert r.returncode != 0
    assert "--tenant-id" in r.stdout
    assert _hashes(db_path) == before


def test_reset_writes_verifiable_hash_and_touches_one_row(db_path):
    from database import verify_password

    before = _hashes(db_path)
    r = _run(db_path, "admin", "--tenant-id", "2", "--password", NEW_PW, "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESET-RECORD: user_id=102" in r.stdout

    after = _hashes(db_path)
    assert verify_password(NEW_PW, after[102])
    assert not verify_password("wrong-password", after[102])
    # Only the targeted row changed.
    assert {k: v for k, v in after.items() if k != 102} == {
        k: v for k, v in before.items() if k != 102
    }


def test_unknown_user_exits_nonzero(db_path):
    before = _hashes(db_path)
    r = _run(db_path, "nobody", "--password", NEW_PW, "--yes")
    assert r.returncode == 1
    assert _hashes(db_path) == before


def _sessions(path: Path) -> dict[int, tuple[int, object]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            sid: (uid, revoked)
            for sid, uid, revoked in conn.execute(
                "SELECT id, user_id, revoked_at FROM sessions"
            )
        }
    finally:
        conn.close()


def test_reset_revokes_only_target_users_sessions(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            "INSERT INTO sessions (id, user_id, token, expires_at) "
            "VALUES (?, ?, ?, '2099-01-01 00:00:00')",
            [(1, 102, "tok-a"), (2, 102, "tok-b"), (3, 103, "tok-c")],
        )
        conn.commit()
    finally:
        conn.close()

    r = _run(db_path, "admin", "--tenant-id", "2", "--password", NEW_PW, "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "revoked_sessions=2" in r.stdout

    after = _sessions(db_path)
    assert after[1][1] is not None and after[2][1] is not None
    assert after[3] == (103, None)
