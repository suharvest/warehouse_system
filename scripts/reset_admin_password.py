#!/usr/bin/env python3
"""管理员密码重置 —— 单文件运维工具，在部署机上直接运行。

使用场景
========
唯一的 admin 忘了密码，且：

* 部署是 ``DEPLOY_MODE=single_tenant``（没有登录页的「找回密码」入口，
  ``/api/auth/reset-password`` 在非 multi_tenant 下直接 403，见 backend/app.py:1258）；
* 或者虽然是 multi_tenant，但 device_id 也丢了，走不了自助恢复。

这时只能在服务器上直接改库。本脚本复用系统自己的 ``hash_password``，
因此写进去的哈希格式与应用完全一致（bcrypt / SHA256 由 ``BCRYPT_ENABLED``
决定），SQLite 和 MySQL 两种后端都适用 —— 连哪个库由容器里现成的
``DATABASE_URL`` / ``DATABASE_PATH`` 环境变量决定，脚本不自己猜。

改完**不需要重启容器**：登录每次都现查库。该用户已有的登录会话会一并吊销
（与应用内改密行为一致）。

用法
====
    # 1) 先看有哪些账号（不改任何东西）
    python reset_admin_password.py --list

    # 2) 交互式重置（推荐，密码不会留在 shell history 里）
    python reset_admin_password.py <用户名>

    # 3) 非交互（无 TTY 时用，注意密码会进 history / ps）
    python reset_admin_password.py <用户名> --password 'NewPass123' --yes

同一用户名在多个租户下可能各有一个账号（username 是 (username, tenant_id)
复合唯一，见 backend/metadata.py:118）。这种情况脚本会列出候选并要求用
``--tenant-id`` 指明，绝不猜。

本脚本不写应用的审计日志（``docker exec`` 跑在应用进程之外，写了也不会进
``docker logs``）。它会在成功后打印一行 RESET-RECORD，请自行留档。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# 定位 backend 包 —— 本脚本被 docker cp 进容器后，位置是任意的（/tmp 等），
# 不能靠相对路径推。按优先级探测，全部落空就明确报错，不做静默降级。
# ---------------------------------------------------------------------------
def _locate_backend() -> Path:
    candidates = []
    env_dir = os.environ.get("WAREHOUSE_BACKEND_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    # 容器内固定布局（Dockerfile.prod:65 COPY backend/ ./backend/）
    candidates.append(Path("/app/backend"))
    # 仓库内布局：scripts/ 的同级
    candidates.append(Path(__file__).resolve().parent.parent / "backend")

    for c in candidates:
        if (c / "database.py").is_file() and (c / "db.py").is_file():
            return c
    sys.exit(
        "找不到 backend 目录（试过：%s）。\n"
        "请用 WAREHOUSE_BACKEND_DIR=/path/to/backend 指定，"
        "或在容器内用 /app/.venv/bin/python 运行本脚本。"
        % ", ".join(str(c) for c in candidates)
    )


_BACKEND = _locate_backend()
sys.path.insert(0, str(_BACKEND))

try:
    from database import hash_password, validate_password_strength  # noqa: E402
    from db import get_engine  # noqa: E402
    from sqlalchemy import text  # noqa: E402
    from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
except ImportError as exc:  # pragma: no cover - 环境问题，给人看的提示
    sys.exit(
        f"导入依赖失败：{exc}\n"
        "本脚本必须用应用自己的解释器运行（容器内是 /app/.venv/bin/python），"
        "系统 python 缺 sqlalchemy/bcrypt。"
    )


class _UnexpectedRowcount(Exception):
    def __init__(self, n: int) -> None:
        super().__init__(n)
        self.n = n


ROLE_HINT = {"admin": "管理员", "operate": "操作员", "view": "只读"}


def _fetch_users(username: str | None = None) -> list[dict]:
    """列出用户。带上租户名便于区分同名账号；tenants 表缺失时降级为只查 users。"""
    sql_with_tenant = """
        SELECT u.id, u.username, u.role, u.is_disabled, u.tenant_id,
               t.name AS tenant_name
        FROM users u
        LEFT JOIN tenants t ON t.id = u.tenant_id
        {where}
        ORDER BY u.tenant_id, u.username
    """
    sql_plain = """
        SELECT id, username, role, is_disabled, tenant_id, NULL AS tenant_name
        FROM users {where} ORDER BY username
    """
    where = "WHERE u.username = :u" if username else ""
    params = {"u": username} if username else {}

    with get_engine().connect() as conn:
        try:
            rows = conn.execute(
                text(sql_with_tenant.format(where=where)), params
            ).mappings().all()
        except Exception:
            where_plain = "WHERE username = :u" if username else ""
            rows = conn.execute(
                text(sql_plain.format(where=where_plain)), params
            ).mappings().all()
    return [dict(r) for r in rows]


def _print_users(users: list[dict]) -> None:
    if not users:
        print("（没有任何用户）")
        return
    print(f"{'ID':>4}  {'用户名':<20} {'角色':<10} {'租户':<20} 状态")
    print("-" * 72)
    for u in users:
        role = f"{u['role']}({ROLE_HINT.get(u['role'], '?')})"
        tenant = u.get("tenant_name") or (
            f"#{u['tenant_id']}" if u.get("tenant_id") is not None else "-"
        )
        state = "已禁用" if u["is_disabled"] else "启用"
        print(f"{u['id']:>4}  {u['username']:<20} {role:<10} {str(tenant):<20} {state}")


def _prompt_password() -> str:
    if not sys.stdin.isatty():
        sys.exit(
            "当前没有 TTY，无法交互输入密码。\n"
            "  用 docker exec -it ... 加上 -i -t，或者改用 --password '<新密码>' --yes。"
        )
    while True:
        pw = getpass.getpass("新密码：")
        if pw != getpass.getpass("再输一遍："):
            print("两次输入不一致，重来。")
            continue
        return pw


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="重置仓管系统用户密码（直接改库，用于 admin 忘密码的恢复）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("username", nargs="?", help="要重置的用户名")
    p.add_argument("--list", action="store_true", help="只列出所有用户，不做任何修改")
    p.add_argument("--tenant-id", type=int, help="同名用户跨租户时用它指定是哪一个")
    p.add_argument("--password", help="新密码（不给则交互输入；给了会留在 shell history）")
    p.add_argument("--enable", action="store_true", help="顺便把该账号的 is_disabled 清零")
    p.add_argument("--allow-weak", action="store_true",
                   help="跳过密码强度校验（应急恢复用，改完请尽快在界面里改成强密码）")
    p.add_argument("--yes", "-y", action="store_true", help="跳过确认提示")
    args = p.parse_args(argv)

    url = os.environ.get("DATABASE_URL") or f"sqlite:///{os.environ.get('DATABASE_PATH', '(默认路径)')}"
    # 打码：URL 里可能带 MySQL 口令
    safe_url = url
    if "@" in safe_url and "//" in safe_url:
        head, tail = safe_url.split("//", 1)
        if "@" in tail:
            cred, host = tail.split("@", 1)
            user = cred.split(":", 1)[0]
            safe_url = f"{head}//{user}:***@{host}"
    print(f"[db] {safe_url}")

    if args.list or not args.username:
        _print_users(_fetch_users())
        if not args.list:
            print("\n用法：python reset_admin_password.py <用户名>")
        return

    matches = _fetch_users(args.username)
    if not matches:
        print(f"没有找到用户名为 {args.username!r} 的账号。现有账号：")
        _print_users(_fetch_users())
        sys.exit(1)

    if args.tenant_id is not None:
        matches = [m for m in matches if m.get("tenant_id") == args.tenant_id]
        if not matches:
            sys.exit(f"租户 {args.tenant_id} 下没有用户 {args.username!r}。")

    if len(matches) > 1:
        print(f"用户名 {args.username!r} 在多个租户下都存在，请用 --tenant-id 指定：")
        _print_users(matches)
        sys.exit(1)

    target = matches[0]
    _print_users([target])
    if target["role"] != "admin":
        print(f"\n注意：该账号角色是 {target['role']}，不是 admin。")
    if target["is_disabled"] and not args.enable:
        print("\n注意：该账号处于**禁用**状态，改了密码也登录不了。加 --enable 一并启用。")

    pw = args.password or _prompt_password()
    err = validate_password_strength(pw)
    if err:
        if not args.allow_weak:
            sys.exit(f"密码不符合要求：{err}（应急可加 --allow-weak 跳过）")
        print(f"\n警告：密码强度不达标（{err}），因 --allow-weak 继续。请尽快在界面里改掉。")

    if not args.yes:
        if not sys.stdin.isatty():
            sys.exit("无 TTY 时必须加 --yes 明确确认。")
        action = "重置密码" + ("并启用账号" if args.enable else "")
        if input(f"\n确认对 {target['username']}(id={target['id']}) {action}？[y/N] ").strip().lower() != "y":
            sys.exit("已取消，未做任何修改。")

    sets = ["password_hash = :h"]
    params = {"h": hash_password(pw), "id": target["id"]}
    if args.enable:
        sets.append("is_disabled = 0")
    try:
        with get_engine().begin() as conn:
            n = conn.execute(
                text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id"), params
            ).rowcount
            if n != 1:
                # 抛异常让 begin() 回滚，不留半截修改。
                raise _UnexpectedRowcount(n)
            # 与应用自身改密一致（backend/app.py 更新用户时的会话吊销）：
            # 旧密码下已登录的会话全部作废，与改密在同一事务内完成。
            revoked = conn.execute(
                text(
                    "UPDATE sessions SET revoked_at = :now "
                    "WHERE user_id = :id AND revoked_at IS NULL"
                ),
                {"now": datetime.now(), "id": target["id"]},
            ).rowcount
    except _UnexpectedRowcount as exc:
        sys.exit(
            f"异常：本次 UPDATE 影响了 {exc.n} 行（预期 1 行），已回滚，请人工核查数据库。"
        )

    print(f"\n完成。{target['username']} 的密码已重置，直接用新密码登录即可，不用重启容器。")
    print(
        "RESET-RECORD: user_id=%s username=%s tenant_id=%s enabled=%s "
        "revoked_sessions=%s (本脚本不写应用审计日志，请自行留档)"
        % (target["id"], target["username"], target.get("tenant_id"),
           bool(args.enable), revoked)
    )


if __name__ == "__main__":
    try:
        main()
    except SQLAlchemyError as exc:
        # 连不上库 / 表不存在，是环境问题不是 bug，给一句人话而不是 traceback。
        sys.exit(
            f"数据库操作失败：{type(exc).__name__}: {exc}\n"
            "请确认 DATABASE_URL / DATABASE_PATH 指向的是这套部署正在用的库"
            "（在容器里跑就会自动继承正确的值）。"
        )
