# Start SyncPulse Process A: FastAPI Web/API Server + UDP Heartbeat Receiver
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment not found at $PythonExe. Run 'python -m venv .venv' and install backend\requirements.txt first."
}

$ApiHost = if ($env:SYNCPULSE_API_HOST) { $env:SYNCPULSE_API_HOST } else { "0.0.0.0" }
$ApiPort = if ($env:SYNCPULSE_API_PORT) { $env:SYNCPULSE_API_PORT } else { "8000" }

Write-Host "Starting SyncPulse API Server on http://${ApiHost}:${ApiPort}" -ForegroundColor Cyan
Write-Host "Local access: http://127.0.0.1:${ApiPort}" -ForegroundColor Green

try {
    $LanIps = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown" } |
        Select-Object -ExpandProperty IPAddress -Unique
    foreach ($ip in $LanIps) {
        Write-Host "iPhone Safari (same Wi-Fi): http://${ip}:${ApiPort}" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Run 'ipconfig' to find your local IPv4 address for iPhone Safari access." -ForegroundColor Yellow
}

& $PythonExe -m uvicorn backend.app:app --host $ApiHost --port $ApiPort
