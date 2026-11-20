# Start SyncPulse Process B: Backup Worker (TCP Server + UDP Heartbeat Sender)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment not found at $PythonExe. Run 'python -m venv .venv' and install backend\requirements.txt first."
}

Write-Host "Starting SyncPulse Backup Worker (TCP 127.0.0.1:9101, UDP -> 127.0.0.1:9102)..." -ForegroundColor Cyan
& $PythonExe -m backend.worker
