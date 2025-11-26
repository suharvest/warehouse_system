#
# MCP 服务启动脚本 (Windows PowerShell)
#
# 使用方法:
#   1. 配置 MCP_ENDPOINT（在下方或通过环境变量）
#   2. 运行: .\start_mcp.ps1
#

# ============ MCP 配置 ============
# 设置 MCP WebSocket 端点地址
# 如果环境变量已设置，则使用环境变量的值
if (-not $env:MCP_ENDPOINT) {
    $env:MCP_ENDPOINT = "ws://localhost:8080/mcp"
}

# ============ 脚本逻辑 ============

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "================================"
Write-Host "  MCP 服务启动脚本"
Write-Host "================================"
Write-Host ""
Write-Host "MCP 端点: $env:MCP_ENDPOINT"
Write-Host ""

# 检查是否安装了 uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "错误: 未找到 uv，请先安装 uv" -ForegroundColor Red
    Write-Host "安装命令: irm https://astral.sh/uv/install.ps1 | iex"
    exit 1
}

# 检查后端服务是否运行
Write-Host "检查后端服务..."
try {
    $response = Invoke-WebRequest -Uri "http://localhost:2124/api/dashboard/stats" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    Write-Host "后端服务运行正常 (端口 2124)" -ForegroundColor Green
} catch {
    Write-Host "警告: 后端服务未运行，MCP 功能可能受限" -ForegroundColor Yellow
    Write-Host "请先启动后端服务: uv run python run_backend.py"
    Write-Host ""
}

# 启动 MCP 服务
Write-Host "启动 MCP 服务..."
Write-Host ""

try {
    uv run python mcp_pipe.py warehouse_mcp.py
} catch {
    Write-Host "MCP 服务启动失败: $_" -ForegroundColor Red
    exit 1
} finally {
    Write-Host ""
    Write-Host "MCP 服务已停止"
}
