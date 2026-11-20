# Run SyncPulse automated unit, integration, and fault-injection tests
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$PytestExe = Join-Path $ProjectRoot ".venv\Scripts\pytest.exe"
if (-not (Test-Path $PytestExe)) {
    Write-Error "pytest not found at $PytestExe. Install backend\requirements.txt first."
}

& $PytestExe -v @args
