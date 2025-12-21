#!/bin/bash

echo "================================"
echo "  仓库管理系统 - 启动脚本"
echo "================================"
echo ""

# 解析参数
USE_VITE=false
for arg in "$@"; do
    case $arg in
        --vite)
            USE_VITE=true
            shift
            ;;
    esac
done

# 检查是否安装了 uv
if ! command -v uv &> /dev/null; then
    echo "错误: 未找到 uv，请先安装 uv"
    echo "安装命令: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# 如果使用 Vite，检查 npm
if [ "$USE_VITE" = true ]; then
    if ! command -v npm &> /dev/null; then
        echo "错误: 未找到 npm，请先安装 Node.js"
        exit 1
    fi
    # 检查是否需要安装依赖
    if [ ! -d "frontend/node_modules" ]; then
        echo "正在安装前端依赖..."
        cd frontend && npm install && cd ..
    fi
fi

# 初始化数据库（仅当数据库不存在时）
if [ ! -f "backend/warehouse.db" ]; then
    echo "正在初始化数据库..."
    cd backend
    uv run python database.py
    if [ $? -ne 0 ]; then
        echo "数据库初始化失败"
        exit 1
    fi
    cd ..
    echo "数据库初始化完成！"
else
    echo "数据库已存在，跳过初始化"
fi

# 清理函数 - 确保所有子进程都被终止
cleanup() {
    echo ''
    echo '正在停止服务...'

    # 终止已知的 PID
    [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null

    # 等待一下让进程正常退出
    sleep 1

    # 强制终止可能残留的进程（按端口）
    lsof -ti:2124 | xargs kill -9 2>/dev/null
    lsof -ti:2125 | xargs kill -9 2>/dev/null

    # 强制终止可能残留的进程（按名称）
    pkill -9 -f "run_backend.py" 2>/dev/null
    pkill -9 -f "frontend/server.py" 2>/dev/null
    pkill -9 -f "vite" 2>/dev/null

    echo '所有服务已停止'
    exit 0
}

# 在启动服务前设置信号处理
trap cleanup INT TERM EXIT

echo ""
echo "启动服务..."
echo ""

# 先清理可能残留的进程
lsof -ti:2124 | xargs kill -9 2>/dev/null
lsof -ti:2125 | xargs kill -9 2>/dev/null
sleep 1

# 启动后端服务
echo "启动后端服务 (端口 2124)..."
uv run python run_backend.py &
BACKEND_PID=$!

sleep 2

# 启动前端服务
echo "启动前端服务 (端口 2125)..."
if [ "$USE_VITE" = true ]; then
    echo "使用 Vite 开发服务器 (热更新模式)..."
    cd frontend && npm run dev &
    FRONTEND_PID=$!
    cd ..
else
    python3 frontend/server.py &
    FRONTEND_PID=$!
fi

sleep 2

echo ""
echo "================================"
echo "  服务启动成功！"
echo "================================"
echo ""
echo "后端 API: http://localhost:2124"
echo "API 文档: http://localhost:2124/docs"
echo "前端页面: http://localhost:2125"
echo ""
if [ "$USE_VITE" = true ]; then
    echo "前端模式: Vite 开发服务器 (支持热更新)"
else
    echo "前端模式: Python 静态服务器"
    echo "提示: 使用 --vite 参数启用热更新模式"
fi
echo ""
echo "请在浏览器中打开: http://localhost:2125"
echo ""
echo "如需启动 MCP 服务，请运行: cd mcp && ./start_mcp.sh"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo ""

# 等待子进程
wait
