#
# MCP Service Startup Script (Windows PowerShell)
#
# Usage:
#   1. Configure MCP_ENDPOINT (below or via environment variable)
#   2. Run: .\start_mcp.ps1
#

# ============ MCP Configuration ============
# Set MCP WebSocket endpoint address
# If environment variable is set, use its value
if (-not $env:MCP_ENDPOINT) {
    $env:MCP_ENDPOINT = "ws://localhost:8080/mcp"
}

# ============ Script Logic ============

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "================================"
Write-Host "  MCP Service Startup Script"
Write-Host "================================"
Write-Host ""
Write-Host "MCP Endpoint: $env:MCP_ENDPOINT"
Write-Host ""

# Check if uv is installed
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue

# If not in PATH, check UV_HOME environment variable
if (-not $uvCommand -and $env:UV_HOME) {
    $uvExe = Join-Path $env:UV_HOME "uv.exe"
    if (Test-Path $uvExe) {
        $env:PATH = "$env:UV_HOME;$env:PATH"
        Write-Host "Found uv from UV_HOME: $uvExe" -ForegroundColor Green
        $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    }
}

# If still not found, check common install locations
if (-not $uvCommand) {
    $uvPaths = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:LOCALAPPDATA\uv\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )
    foreach ($path in $uvPaths) {
        if (Test-Path $path) {
            $uvDir = Split-Path -Parent $path
            $env:PATH = "$uvDir;$env:PATH"
            Write-Host "Found uv: $path" -ForegroundColor Green
            $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
            break
        }
    }
}

if (-not $uvCommand) {
    Write-Host "Error: uv not found, please install uv first" -ForegroundColor Red
    Write-Host "Install command: irm https://astral.sh/uv/install.ps1 | iex"
    Write-Host "Or set UV_HOME environment variable to uv install directory"
    exit 1
}

# Check if backend service is running
Write-Host "Checking backend service..."
try {
    $response = Invoke-WebRequest -Uri "http://localhost:2124/api/dashboard/stats" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    Write-Host "Backend service is running (port 2124)" -ForegroundColor Green
} catch {
    Write-Host "Warning: Backend service is not running, MCP features may be limited" -ForegroundColor Yellow
    Write-Host "Please start backend first: uv run python run_backend.py"
    Write-Host ""
}

# Start MCP service
Write-Host "Starting MCP service..."
Write-Host ""

try {
    uv run python mcp_pipe.py warehouse_mcp.py
} catch {
    Write-Host "MCP service startup failed: $_" -ForegroundColor Red
    exit 1
} finally {
    Write-Host ""
    Write-Host "MCP service stopped"
}
