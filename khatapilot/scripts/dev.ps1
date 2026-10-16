<#
.SYNOPSIS
  Start KhataPilot locally: the sandbox (port 8100), the backend (8000) and the frontend (5173),
  each in its own PowerShell window.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1          # start everything
  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Seed    # reset and load the demo shop first
  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -NoFrontend
#>
param(
    [switch]$Seed,
    [switch]$NoFrontend
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Host "Creating the virtual environment (.venv) and installing dependencies..." -ForegroundColor Cyan
    python -m venv .venv
    & $Py -m pip install --upgrade pip
    & $Py -m pip install -r requirements-dev.txt
}
if (-not (Test-Path (Join-Path $Root ".env"))) {
    & $Py scripts\make_env.py
}

function Start-InWindow([string]$Title, [string]$Command) {
    $script = "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$Root'; $Command"
    Start-Process powershell -ArgumentList @("-NoExit", "-Command", $script) | Out-Null
}

function Wait-Healthy([string]$Url, [string]$Name) {
    for ($i = 0; $i -lt 40; $i++) {
        try {
            Invoke-RestMethod -Uri $Url -TimeoutSec 2 | Out-Null
            Write-Host "$Name is up." -ForegroundColor Green
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "$Name did not become healthy at $Url"
}

Start-InWindow "KhataPilot sandbox :8100" "& '$Py' -m uvicorn sandbox.app:create_app --factory --port 8100"
Wait-Healthy "http://127.0.0.1:8100/health" "Sandbox"

if ($Seed) {
    & $Py scripts\seed.py
}
# Fraud Sentinel: trained once (a few seconds), then reused; without it the guard uses its rule-based score.
& $Py scripts\train_models.py --if-missing
# The credit model: trained once (a few seconds); without it, credit profiles and loan matching are unavailable.
& $Py scripts\train_credit.py --if-missing

Start-InWindow "KhataPilot backend :8000" "& '$Py' -m uvicorn app.main:create_app --factory --app-dir backend --port 8000 --reload --reload-dir backend"
Wait-Healthy "http://127.0.0.1:8000/api/health" "Backend"

if (-not $NoFrontend -and (Test-Path (Join-Path $Root "frontend\package.json"))) {
    if (-not (Test-Path (Join-Path $Root "frontend\node_modules"))) {
        Push-Location frontend
        npm install
        Pop-Location
    }
    Start-InWindow "KhataPilot frontend :5173" "Set-Location frontend; npm run dev"
}

Write-Host ""
Write-Host "KhataPilot is running:" -ForegroundColor Cyan
Write-Host "  Web app          http://localhost:5173"
Write-Host "  Backend API docs http://127.0.0.1:8000/docs"
Write-Host "  Sandbox (SIMULATED providers) http://127.0.0.1:8100/docs"
Write-Host "Close the three windows to stop."
