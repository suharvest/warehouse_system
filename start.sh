#!/bin/bash

echo "================================"
echo "  仓库管理系统 - 启动脚本"
echo "================================"
echo ""

# 检查是否安装了 uv
if ! command -v uv &> /dev/null; then
    echo "错误: 未找到 uv，请先安装 uv"
    echo "安装命令: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
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

echo ""
echo "启动服务..."
echo ""

# 启动后端服务
echo "启动后端服务 (端口 2124)..."
uv run python run_backend.py &
BACKEND_PID=$!

sleep 2

# 启动前端服务
echo "启动前端服务 (端口 2125)..."
python3 frontend/server.py &
FRONTEND_PID=$!

sleep 2

# 启动 MCP 服务
echo "启动 MCP 服务..."
cd mcp
uv run python mcp_pipe.py warehouse_mcp.py &
MCP_PID=$!
cd ..

sleep 1

echo ""
echo "================================"
echo "  服务启动成功！"
echo "================================"
echo ""
echo "后端 API: http://localhost:2124"
echo "前端页面: http://localhost:2125"
echo "MCP 服务: 已启动"
echo ""
echo "请在浏览器中打开: http://localhost:2125"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo ""

# 等待中断信号
trap "echo ''; echo '正在停止服务...'; kill $BACKEND_PID $FRONTEND_PID $MCP_PID 2>/dev/null; exit" INT TERM

wait
