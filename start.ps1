#
# 仓库管理系统 - 启动脚本 (Windows PowerShell)
#
# 使用方法: .\start.ps1 [-Vite]
# 参数: -Vite  使用 Vite 开发服务器 (热更新模式)
#

param(
    [switch]$Vite
)

Write-Host "================================"
Write-Host "  仓库管理系统 - 启动脚本"
Write-Host "================================"
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# 检查是否安装了 uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "错误: 未找到 uv，请先安装 uv" -ForegroundColor Red
    Write-Host "安装命令: irm https://astral.sh/uv/install.ps1 | iex"
    exit 1
}

# 如果使用 Vite，检查 npm
if ($Vite) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "错误: 未找到 npm，请先安装 Node.js" -ForegroundColor Red
        exit 1
    }
    # 检查是否需要安装依赖
    if (-not (Test-Path "frontend\node_modules")) {
        Write-Host "正在安装前端依赖..."
        Push-Location frontend
        npm install
        Pop-Location
    }
}

# 初始化数据库（仅当数据库不存在时）
if (-not (Test-Path "backend\warehouse.db")) {
    Write-Host "正在初始化数据库..."
    Push-Location backend
    uv run python database.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "数据库初始化失败" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
    Write-Host "数据库初始化完成！" -ForegroundColor Green
} else {
    Write-Host "数据库已存在，跳过初始化"
}

Write-Host ""
Write-Host "启动服务..."
Write-Host ""

# 停止可能残留的进程
Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "run_backend|server\.py"
} | Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 1

# 启动后端服务
Write-Host "启动后端服务 (端口 2124)..."
$backend = Start-Process -FilePath "uv" -ArgumentList "run", "python", "run_backend.py" -PassThru -NoNewWindow

Start-Sleep -Seconds 2

# 启动前端服务
Write-Host "启动前端服务 (端口 2125)..."
if ($Vite) {
    Write-Host "使用 Vite 开发服务器 (热更新模式)..."
    Push-Location frontend
    $frontend = Start-Process -FilePath "npm" -ArgumentList "run", "dev" -PassThru -NoNewWindow
    Pop-Location
} else {
    $frontend = Start-Process -FilePath "python" -ArgumentList "frontend\server.py" -PassThru -NoNewWindow
}

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "================================"
Write-Host "  服务启动成功！"
Write-Host "================================"
Write-Host ""
Write-Host "后端 API: http://localhost:2124"
Write-Host "API 文档: http://localhost:2124/docs"
Write-Host "前端页面: http://localhost:2125"
Write-Host ""
if ($Vite) {
    Write-Host "前端模式: Vite 开发服务器 (支持热更新)" -ForegroundColor Cyan
} else {
    Write-Host "前端模式: Python 静态服务器"
    Write-Host "提示: 使用 -Vite 参数启用热更新模式" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "请在浏览器中打开: http://localhost:2125"
Write-Host ""
Write-Host "按任意键停止所有服务..."

# 等待用户按键
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

Write-Host ""
Write-Host "正在停止服务..."

# 停止进程
if ($backend -and -not $backend.HasExited) {
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
}
if ($frontend -and -not $frontend.HasExited) {
    Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
}

# 清理残留进程
Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "run_backend|server\.py"
} | Stop-Process -Force -ErrorAction SilentlyContinue

# 清理 Vite/Node 进程
Get-Process -Name node* -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "vite"
} | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "所有服务已停止" -ForegroundColor Green
