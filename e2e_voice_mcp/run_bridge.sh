#!/usr/bin/env bash
# 启动 mcp_pipe.py 外拨官方 MCP 接入点，把 warehouse_mcp.py 的工具注册给官方 agent。
# 接入点/凭据来自 .env.local（会轮换——失效时改那里）。日志 → logs/mcp_pipe.log。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
[ -f "$HERE/.env.local" ] && source "$HERE/.env.local"
: "${MCP_ENDPOINT:?未设置 MCP_ENDPOINT（见 .env.local）}"
: "${WAREHOUSE_API_KEY:?未设置 WAREHOUSE_API_KEY（见 .env.local）}"
mkdir -p "$HERE/logs"
echo "mcp_pipe → 官方接入点 ${MCP_ENDPOINT%%\?*}?token=… → $HERE/logs/mcp_pipe.log"
cd "$REPO"
MCP_PIPE_LOG_DIR="$HERE/logs" PORT="${PORT:-2124}" \
  exec uv run python mcp/mcp_pipe.py mcp/warehouse_mcp.py >"$HERE/logs/mcp_pipe.log" 2>&1
