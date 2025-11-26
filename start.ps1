#
# 仓库管理系统 - 启动脚本 (Windows PowerShell)
#
# 使用方法: .\start.ps1
#

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
$frontend = Start-Process -FilePath "python" -ArgumentList "frontend\server.py" -PassThru -NoNewWindow

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

Write-Host "所有服务已停止" -ForegroundColor Green
