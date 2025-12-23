#
# Warehouse Management System - Startup Script (Windows PowerShell)
#
# Usage: .\start.ps1 [-Vite]
# Args: -Vite  Use Vite dev server (hot reload mode)
#

param(
    [switch]$Vite
)

Write-Host "================================"
Write-Host "  Warehouse System - Startup"
Write-Host "================================"
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

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

# Check npm availability
$hasNpm = Get-Command npm -ErrorAction SilentlyContinue

if ($Vite) {
    # Vite mode requires npm
    if (-not $hasNpm) {
        Write-Host "Error: npm not found, please install Node.js first" -ForegroundColor Red
        exit 1
    }
    # Check if dependencies need to be installed
    if (-not (Test-Path "frontend\node_modules")) {
        Write-Host "Installing frontend dependencies..."
        Push-Location frontend
        npm install
        Pop-Location
    }
} else {
    # Production mode: check if dist exists, if not try to build
    if (-not (Test-Path "frontend\dist\index.html")) {
        if ($hasNpm) {
            Write-Host "Building frontend (first time)..."
            Push-Location frontend
            if (-not (Test-Path "node_modules")) {
                Write-Host "Installing dependencies..."
                npm install
            }
            npm run build
            Pop-Location
        } else {
            Write-Host "Warning: frontend/dist not found and npm not available" -ForegroundColor Yellow
            Write-Host "Frontend may not work properly. Install Node.js and run:" -ForegroundColor Yellow
            Write-Host "  cd frontend && npm install && npm run build" -ForegroundColor Yellow
        }
    }
}

# Initialize database (only if it doesn't exist)
if (-not (Test-Path "backend\warehouse.db")) {
    Write-Host "Initializing database..."
    Push-Location backend
    uv run python database.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Database initialization failed" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
    Write-Host "Database initialized!" -ForegroundColor Green
} else {
    Write-Host "Database exists, skipping initialization"
}

Write-Host ""
Write-Host "Starting services..."
Write-Host ""

# Function to kill process by port
function Stop-ProcessByPort {
    param([int]$Port)
    $connections = netstat -ano 2>$null | Select-String ":$Port\s" | Select-String "LISTENING"
    foreach ($conn in $connections) {
        $parts = $conn -split '\s+'
        $pid = $parts[-1]
        if ($pid -match '^\d+$' -and $pid -ne '0') {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
}

# Stop any leftover processes by port (most reliable on Windows)
Write-Host "Cleaning up any leftover processes..."
Stop-ProcessByPort -Port 2124
Stop-ProcessByPort -Port 2125

Start-Sleep -Seconds 1

# Start backend service
Write-Host "Starting backend service (port 2124)..."
$backend = Start-Process -FilePath "uv" -ArgumentList "run", "python", "run_backend.py" -PassThru -NoNewWindow

Start-Sleep -Seconds 2

# Start frontend service
Write-Host "Starting frontend service (port 2125)..."
if ($Vite) {
    Write-Host "Using Vite dev server (hot reload mode)..."
    Push-Location frontend
    $frontend = Start-Process -FilePath "npm" -ArgumentList "run", "dev" -PassThru -NoNewWindow
    Pop-Location
} else {
    $frontend = Start-Process -FilePath "python" -ArgumentList "frontend\server.py" -PassThru -NoNewWindow
}

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "================================"
Write-Host "  Services started!"
Write-Host "================================"
Write-Host ""
Write-Host "Backend API: http://localhost:2124"
Write-Host "API Docs: http://localhost:2124/docs"
Write-Host "Frontend: http://localhost:2125"
Write-Host ""
if ($Vite) {
    Write-Host "Frontend mode: Vite dev server (hot reload)" -ForegroundColor Cyan
} else {
    Write-Host "Frontend mode: Python static server"
    Write-Host "Tip: Use -Vite flag to enable hot reload mode" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "Open in browser: http://localhost:2125"
Write-Host ""
Write-Host "Press Ctrl+C to stop all services..."
Write-Host ""

# Function to clean up all services
function Stop-AllServices {
    Write-Host ""
    Write-Host "Stopping services..."

    # Stop tracked processes
    if ($backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    }
    if ($frontend -and -not $frontend.HasExited) {
        Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
    }

    # Kill by port (more reliable on Windows)
    Stop-ProcessByPort -Port 2124
    Stop-ProcessByPort -Port 2125

    # Clean up leftover python processes
    Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "run_backend|server\.py"
    } | Stop-Process -Force -ErrorAction SilentlyContinue

    # Clean up Vite/Node processes
    Get-Process -Name node* -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "vite"
    } | Stop-Process -Force -ErrorAction SilentlyContinue

    Write-Host "All services stopped" -ForegroundColor Green
}

# Register Ctrl+C handler
try {
    # Wait for backend process to exit (keeps script running)
    if ($backend) {
        $backend.WaitForExit()
    }
} finally {
    Stop-AllServices
}
