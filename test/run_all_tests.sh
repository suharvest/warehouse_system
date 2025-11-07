#!/bin/bash

echo "======================================"
echo "  仓库管理系统 - 运行所有测试"
echo "======================================"
echo ""

# 获取脚本所在目录的父目录（项目根目录）
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "项目根目录: $PROJECT_ROOT"
echo ""

# 检查数据库是否存在
if [ ! -f "backend/warehouse.db" ]; then
    echo "⚠️  数据库不存在，正在初始化..."
    cd backend
    uv run python database.py
    cd ..
    echo "✅ 数据库初始化完成"
    echo ""
fi

# 测试1: MCP 工具测试
echo "======================================"
echo "测试 1: MCP 工具测试"
echo "======================================"
python3 test/test_mcp.py
TEST1_RESULT=$?

if [ $TEST1_RESULT -eq 0 ]; then
    echo "✅ MCP 工具测试通过"
else
    echo "❌ MCP 工具测试失败"
fi

echo ""
echo "======================================"
echo "测试 2: API 接口测试"
echo "======================================"
echo ""

# 检查后端服务是否运行
if lsof -ti:2124 > /dev/null 2>&1; then
    echo "✅ 后端服务已运行，开始测试 API..."
    python3 test/test_api.py
    TEST2_RESULT=$?

    if [ $TEST2_RESULT -eq 0 ]; then
        echo "✅ API 接口测试通过"
    else
        echo "❌ API 接口测试失败"
    fi
else
    echo "⚠️  后端服务未运行"
    echo "正在启动后端服务..."

    uv run python run_backend.py &
    BACKEND_PID=$!
    sleep 3

    if lsof -ti:2124 > /dev/null 2>&1; then
        echo "✅ 后端服务启动成功，开始测试 API..."
        python3 test/test_api.py
        TEST2_RESULT=$?

        if [ $TEST2_RESULT -eq 0 ]; then
            echo "✅ API 接口测试通过"
        else
            echo "❌ API 接口测试失败"
        fi

        # 停止后端服务
        echo ""
        echo "正在停止后端服务..."
        kill $BACKEND_PID 2>/dev/null
        sleep 1
        echo "✅ 后端服务已停止"
    else
        echo "❌ 后端服务启动失败"
        TEST2_RESULT=1
    fi
fi

echo ""
echo "======================================"
echo "测试总结"
echo "======================================"
echo ""

if [ $TEST1_RESULT -eq 0 ] && [ $TEST2_RESULT -eq 0 ]; then
    echo "🎉 所有测试通过！"
    echo ""
    echo "提示："
    echo "  - 前端页面: http://localhost:2125"
    echo "  - 后端 API: http://localhost:2124"
    echo ""
    exit 0
else
    echo "⚠️  部分测试失败"
    echo ""
    echo "测试结果："
    [ $TEST1_RESULT -eq 0 ] && echo "  ✅ MCP 工具测试" || echo "  ❌ MCP 工具测试"
    [ $TEST2_RESULT -eq 0 ] && echo "  ✅ API 接口测试" || echo "  ❌ API 接口测试"
    echo ""
    exit 1
fi
