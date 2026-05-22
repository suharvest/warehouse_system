"""Spawn / teardown backend uvicorn + sqlite seed.

Phase 1 implementation:
- 用 mkstemp 起独立 sqlite
- 起 backend (它启动时自跑 alembic upgrade + _seed_base_data)
- 等 /health
- 直接 sqlite3 写入 materials / batches / api_keys（hashed key）
- snapshot copy 作为写类 case 复位基线
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SALT = "warehouse_api_salt_2024"  # 同 backend/database.py:1040


def hash_api_key(key: str) -> str:
    return hashlib.sha256(f"{key}:{SALT}".encode()).hexdigest()


@dataclass
class BackendHandle:
    proc: subprocess.Popen
    port: int
    db_path: Path
    snapshot_path: Path
    api_key_plaintext: str

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _find_free_port(start: int = 12450) -> int:
    for p in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free port")


def _wait_for_http(url: str, timeout: float = 30.0) -> None:
    t0 = time.time()
    last_err = None
    while time.time() - t0 < timeout:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return
        except Exception as e:
            last_err = e
        time.sleep(0.3)
    raise TimeoutError(f"backend not ready in {timeout}s: {last_err}")


def _apply_seed(db_path: Path, profile: dict, api_key_plaintext: str) -> None:
    """直接对 sqlite 插数据。backend 启动后已建表 + 种了 tenant1/wh1。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        # warehouses 额外
        for wh in profile.get("warehouses", []) or []:
            cur.execute(
                "INSERT OR IGNORE INTO warehouses (id, slug, name, is_default, is_disabled, tenant_id) "
                "VALUES (?, ?, ?, 0, 0, 1)",
                (wh["id"], wh["slug"], wh.get("name", wh["slug"])),
            )

        # materials + batches
        for mat in profile.get("materials", []) or []:
            cur.execute(
                "INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, "
                "is_disabled, warehouse_id, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, 1)",
                (
                    mat["name"],
                    mat["sku"],
                    mat.get("category", "未分类"),
                    int(mat.get("stock", 0)),
                    mat.get("unit", "个"),
                    mat.get("safe_stock"),
                    mat.get("location"),
                ),
            )
            material_id = cur.lastrowid

            batches = mat.get("batches") or []
            if batches:
                # 用批次实际数量重算 materials.quantity
                total = 0
                for b in batches:
                    cur.execute(
                        "INSERT INTO batches (batch_no, material_id, quantity, initial_quantity, "
                        "is_exhausted, warehouse_id, tenant_id) "
                        "VALUES (?, ?, ?, ?, 0, 1, 1)",
                        (
                            b["batch_no"],
                            material_id,
                            int(b["quantity"]),
                            int(b.get("initial_quantity", b["quantity"])),
                        ),
                    )
                    total += int(b["quantity"])
                cur.execute(
                    "UPDATE materials SET quantity = ? WHERE id = ?",
                    (total, material_id),
                )
            elif int(mat.get("stock", 0)) > 0:
                # 无显式 batch，但有 stock：自动生成单批
                cur.execute(
                    "INSERT INTO batches (batch_no, material_id, quantity, initial_quantity, "
                    "is_exhausted, warehouse_id, tenant_id) "
                    "VALUES (?, ?, ?, ?, 0, 1, 1)",
                    (
                        f"SEED-{mat['sku']}",
                        material_id,
                        int(mat["stock"]),
                        int(mat["stock"]),
                    ),
                )

        # contacts
        for c in profile.get("contacts", []) or []:
            cur.execute(
                "INSERT INTO contacts (name, is_supplier, is_customer, is_disabled) "
                "VALUES (?, ?, ?, 0)",
                (c["name"], 1 if c.get("is_supplier") else 0, 1 if c.get("is_customer") else 0),
            )

        # API key (system, role=admin so writes also allowed)
        key_hash = hash_api_key(api_key_plaintext)
        cur.execute(
            "INSERT INTO api_keys (name, key_hash, role, is_disabled, is_system, tenant_id) "
            "VALUES (?, ?, ?, 0, 1, 1)",
            ("eval-system", key_hash, "admin"),
        )

        conn.commit()
    finally:
        conn.close()


def start_backend(
    seed_yaml_path: Path,
    profile_name: str = "base",
    port: Optional[int] = None,
) -> BackendHandle:
    fd, db_path_s = tempfile.mkstemp(suffix=".db", prefix="eval_warehouse_")
    os.close(fd)
    db_path = Path(db_path_s)
    db_path.unlink()  # backend will create

    port = port or _find_free_port()
    api_key = "eval-" + secrets.token_hex(16)

    env = {
        **os.environ,
        "DATABASE_PATH": str(db_path),
        "INIT_MOCK_DATA": "false",
        "ENABLE_AUDIT_LOG": "0",
        "PORT": str(port),
        "PYTHONUNBUFFERED": "1",
        "DISABLE_RATE_LIMIT": "1",
        "EVAL_TEST_MODE": "1",
    }

    log_path = db_path.with_suffix(".backend.log")
    log_f = open(log_path, "wb")
    proc = subprocess.Popen(
        ["uv", "run", "--extra", "eval", "python", "backend/app.py"],
        env=env,
        cwd=str(PROJECT_ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )

    try:
        _wait_for_http(f"http://127.0.0.1:{port}/health", timeout=45)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_f.close()
        try:
            print("[backend log tail]:", log_path.read_text(errors="replace")[-2000:], file=sys.stderr)
        except Exception:
            pass
        raise

    # Apply seed AFTER backend boot (tables exist)
    with open(seed_yaml_path, "r", encoding="utf-8") as f:
        seeds = yaml.safe_load(f)
    profiles = seeds.get("profiles", {})
    if profile_name not in profiles:
        raise ValueError(f"profile {profile_name} not in seed.yaml")
    profile = profiles[profile_name]

    # Need to pause backend's connections briefly. NullPool ⇒ each request opens fresh conn,
    # so we can write while backend is idle. Try drain_pool route first if available.
    try:
        requests.post(f"http://127.0.0.1:{port}/api/_test/drain_pool", timeout=2)
    except Exception:
        pass

    _apply_seed(db_path, profile, api_key)

    # snapshot
    snapshot = db_path.with_suffix(".snapshot.db")
    shutil.copy(str(db_path), str(snapshot))

    return BackendHandle(
        proc=proc,
        port=port,
        db_path=db_path,
        snapshot_path=snapshot,
        api_key_plaintext=api_key,
    )


def reset_db(handle: BackendHandle) -> None:
    """文件级 snapshot copy 覆盖 live db，复位写类用例。"""
    try:
        requests.post(f"http://127.0.0.1:{handle.port}/api/_test/drain_pool", timeout=2)
    except Exception:
        pass
    shutil.copy(str(handle.snapshot_path), str(handle.db_path))


def stop_backend(handle: BackendHandle, keep_db: bool = False) -> None:
    try:
        handle.proc.terminate()
        handle.proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        handle.proc.kill()
    except Exception:
        pass

    if not keep_db:
        for p in (handle.db_path, handle.snapshot_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
