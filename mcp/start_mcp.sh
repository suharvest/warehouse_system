#!/bin/bash
#
# MCP 服务启动脚本 (macOS/Linux)
#
# 使用方法:
#   1. 配置 MCP_ENDPOINT（在下方或通过环境变量）
#   2. 运行: ./start_mcp.sh
#

# ============ MCP 配置 ============
# 设置 MCP WebSocket 端点地址
# 如果环境变量已设置，则使用环境变量的值
if [ -z "$MCP_ENDPOINT" ]; then
    export MCP_ENDPOINT="ws://localhost:8080/mcp"
fi

# ============ 脚本逻辑 ============

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================"
echo "  MCP 服务启动脚本"
echo "================================"
echo ""
echo "MCP 端点: $MCP_ENDPOINT"
echo ""

# 检查是否安装了 uv
if ! command -v uv &> /dev/null; then
    echo "错误: 未找到 uv，请先安装 uv"
    echo "安装命令: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# 检查后端服务是否运行。端口跟随 PORT 环境变量（与 warehouse_mcp.py 的
# api_base_url 推导一致；Docker 部署设 PORT=2125），默认 2124，避免健康检查
# 对着错误端口报"后端未运行"的假警告（issue #2）。
echo "检查后端服务..."
BACKEND_PORT="${PORT:-2124}"
if curl -s "http://localhost:${BACKEND_PORT}/api/dashboard/stats" > /dev/null 2>&1; then
    echo "后端服务运行正常 (端口 ${BACKEND_PORT})"
else
    echo "警告: 后端服务未运行，MCP 功能可能受限"
    echo "请先启动后端服务: uv run python run_backend.py"
    echo ""
fi

# 清理函数
cleanup() {
    echo ''
    echo '正在停止 MCP 服务...'
    [ -n "$MCP_PID" ] && kill $MCP_PID 2>/dev/null
    pkill -f "warehouse_mcp.py" 2>/dev/null
    echo 'MCP 服务已停止'
    exit 0
}

trap cleanup INT TERM EXIT

# 启动 MCP 服务
echo "启动 MCP 服务..."
uv run python mcp_pipe.py warehouse_mcp.py &
MCP_PID=$!

sleep 2

if kill -0 $MCP_PID 2>/dev/null; then
    echo ""
    echo "================================"
    echo "  MCP 服务启动成功！"
    echo "================================"
    echo ""
    echo "MCP 端点: $MCP_ENDPOINT"
    echo ""
    echo "按 Ctrl+C 停止服务"
    echo ""
    wait $MCP_PID
else
    echo "MCP 服务启动失败"
    exit 1
fi
