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
    if [ ! -d "frontend/node_modules" ]; then
        echo "正在安装前端依赖..."
        cd frontend && npm install && cd ..
    fi
fi

# 确保前端已构建（非 vite 模式需要 dist 目录）
if [ "$USE_VITE" = false ] && [ ! -f "frontend/dist/index.html" ]; then
    echo "前端未构建，正在构建..."
    if ! command -v npm &> /dev/null; then
        echo "错误: 未找到 npm，请先安装 Node.js 或使用 --vite 模式"
        exit 1
    fi
    if [ ! -d "frontend/node_modules" ]; then
        cd frontend && npm install && cd ..
    fi
    cd frontend && npm run build && cd ..
    echo "前端构建完成！"
fi

# 初始化数据库（仅当数据库不存在时）
if [ ! -f "backend/warehouse.db" ]; then
    echo "正在初始化数据库..."
    cd backend && uv run python database.py && cd ..
    echo "数据库初始化完成！"
else
    echo "数据库已存在，跳过初始化"
fi

# 清理函数
cleanup() {
    echo ''
    echo '正在停止服务...'
    [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null
    [ -n "$VITE_PID" ] && kill $VITE_PID 2>/dev/null
    sleep 1
    lsof -ti:2124 | xargs kill -9 2>/dev/null
    [ "$USE_VITE" = true ] && lsof -ti:2125 | xargs kill -9 2>/dev/null
    pkill -9 -f "run_backend.py" 2>/dev/null
    echo '所有服务已停止'
    exit 0
}

trap cleanup INT TERM EXIT

echo ""
echo "启动服务..."
echo ""

# 清理残留进程
lsof -ti:2124 | xargs kill -9 2>/dev/null
sleep 1

# 启动后端（同时 serve 前端静态文件）
echo "启动后端服务 (端口 2124)..."
uv run python run_backend.py &
BACKEND_PID=$!

sleep 2

if [ "$USE_VITE" = true ]; then
    # Vite 开发模式：前端走 Vite（热更新），API 走后端
    echo "启动 Vite 开发服务器 (端口 2125, 热更新)..."
    cd frontend && npm run dev &
    VITE_PID=$!
    cd ..
    sleep 2
fi

echo ""
echo "================================"
echo "  服务启动成功！"
echo "================================"
echo ""
echo "后端 API: http://localhost:2124"
echo "API 文档: http://localhost:2124/docs"
if [ "$USE_VITE" = true ]; then
    echo "前端页面: http://localhost:2125 (Vite 热更新)"
    echo ""
    echo "请在浏览器中打开: http://localhost:2125"
else
    echo "前端页面: http://localhost:2124 (后端直接服务)"
    echo ""
    echo "请在浏览器中打开: http://localhost:2124"
    echo "提示: 使用 --vite 参数启用前端热更新模式"
fi
echo ""
echo "MCP 智能体可在 Web 界面「智能体配置」中管理"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo ""

wait
