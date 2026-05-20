"""Route inventory snapshot test.

锁定当前 FastAPI 应用的全部路由（method + path + name + response_model 名 +
status_code + tags）作为重构防护网。任何路由的**新增 / 删除 / 改路径 / 改方法
/ 改响应模型类名 / 改状态码 / 改 tags** 都会导致此测试失败，并打印 diff 让
开发者主动确认。

预期使用：
- 增/删/改路由时，确认 diff 正确后用
      python tests/test_route_inventory.py --update
  重新生成 fixture（注意：不是 `pytest --update-*`，是直接 `python` 跑文件本身）。
- 大型 app.py 拆分到多个 router 文件期间，本测试是"零路由丢失"的硬保证。

如果 fixture 出现意料之外的变化，**先停手**——往往意味着路由声明
（装饰器、include_router、response_model 类名、Depends、status_code）被无意
改动了。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "route_inventory.json"

# 让 `from app import app` 能解析（backend 不是 importable package）
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _collect_routes() -> list[dict]:
    """枚举当前 FastAPI app 的所有 HTTP 路由，按 (path, method) 排序返回。

    跳过 ASGI Mount / WebSocket 等非 HTTP 路由（它们没有 methods）。
    若 Mount 内嵌套了 FastAPI sub-app，下面会在 _walk 中递归展开 sub-app 的 HTTP 路由，
    避免静默漏掉 mounted 子应用。
    """
    from starlette.routing import Mount as _Mount

    from app import app  # type: ignore[import-not-found]

    routes: list[dict] = []

    def _walk(route_iterable, prefix: str = "") -> None:
        for r in route_iterable:
            # 嵌套 sub-app：递归收集（warehouse_system 当前不 mount sub-app，
            # 这是面向未来的防御性兜底）。
            if isinstance(r, _Mount) and getattr(r, "app", None) is not None:
                sub = getattr(r.app, "routes", None)
                if sub:
                    _walk(sub, prefix + (r.path or ""))
                continue
            methods = getattr(r, "methods", None)
            if not methods:
                continue
            methods = sorted(m for m in methods if m not in ("HEAD", "OPTIONS"))
            if not methods:
                continue
            name = getattr(r, "name", "") or ""
            rm = getattr(r, "response_model", None)
            rm_name = rm.__name__ if rm is not None else None
            status_code = getattr(r, "status_code", None)
            tags = sorted(getattr(r, "tags", None) or [])
            for m in methods:
                routes.append({
                    "method": m,
                    "path": prefix + r.path,
                    "name": name,
                    "response_model": rm_name,
                    "status_code": status_code,
                    "tags": tags,
                })

    _walk(app.routes)
    routes.sort(key=lambda x: (x["path"], x["method"]))
    return routes


def _load_fixture() -> list[dict]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _format_diff(actual: list[dict], expected: list[dict]) -> str:
    """生成可读的 diff 描述（只列出新增 / 删除 / 改动的条目）。"""
    def _key(r: dict) -> tuple:
        return (r["method"], r["path"])

    exp_map = {_key(r): r for r in expected}
    act_map = {_key(r): r for r in actual}

    added = sorted(set(act_map) - set(exp_map))
    removed = sorted(set(exp_map) - set(act_map))
    changed: list[tuple] = []
    for k in sorted(set(act_map) & set(exp_map)):
        if act_map[k] != exp_map[k]:
            changed.append((k, exp_map[k], act_map[k]))

    lines: list[str] = []
    if added:
        lines.append(f"新增 {len(added)} 个路由:")
        for k in added:
            r = act_map[k]
            lines.append(f"  + {r['method']:6} {r['path']}  ({r['name']}, response_model={r['response_model']})")
    if removed:
        lines.append(f"丢失 {len(removed)} 个路由:")
        for k in removed:
            r = exp_map[k]
            lines.append(f"  - {r['method']:6} {r['path']}  ({r['name']}, response_model={r['response_model']})")
    if changed:
        lines.append(f"变化 {len(changed)} 个路由:")
        for k, e, a in changed:
            lines.append(f"  ~ {k[0]:6} {k[1]}")
            lines.append(f"      from: {e}")
            lines.append(f"      to:   {a}")
    return "\n".join(lines) if lines else "（无 diff）"


def test_route_inventory_matches_snapshot() -> None:
    """所有 HTTP 路由必须与 fixture 完全一致。"""
    expected = _load_fixture()
    actual = _collect_routes()

    if actual != expected:
        diff = _format_diff(actual, expected)
        instruction = (
            "\n\n如果改动是有意的（新增 / 删除 / 调整路由），请用以下命令更新 fixture：\n"
            "  uv run python tests/test_route_inventory.py --update\n"
            "更新前请先用 git diff 确认 diff 符合预期。"
        )
        pytest.fail(
            f"路由清单与 fixture {FIXTURE_PATH.relative_to(REPO_ROOT)} 不一致：\n{diff}{instruction}"
        )


if __name__ == "__main__":
    # 允许 `python tests/test_route_inventory.py --update` 重生成 fixture。
    # 故意不接 pytest 入口，避免误触发。
    if "--update" in sys.argv:
        routes = _collect_routes()
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FIXTURE_PATH.open("w", encoding="utf-8") as f:
            json.dump(routes, f, indent=2, ensure_ascii=False, sort_keys=True)
        print(f"updated {FIXTURE_PATH} with {len(routes)} routes")
    else:
        print(
            "用法：python tests/test_route_inventory.py --update   # 重新生成 fixture\n"
            "       uv run pytest tests/test_route_inventory.py     # 跑测试"
        )
