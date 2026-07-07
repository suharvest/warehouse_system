#!/usr/bin/env bash
# 启动 warehouse 后端于 :2124，日志写到 logs/backend.log（供 E2E 断言核查工具调用）。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
[ -f "$HERE/.env.local" ] && source "$HERE/.env.local"
mkdir -p "$HERE/logs"
echo "启动 warehouse 后端 :${PORT:-2124} → $HERE/logs/backend.log"
cd "$REPO"
PORT="${PORT:-2124}" exec uv run python run_backend.py >"$HERE/logs/backend.log" 2>&1
