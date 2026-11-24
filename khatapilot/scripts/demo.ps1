<#
.SYNOPSIS
  Keep KhataPilot running for a stage demo, for hours.

  Starts the sandbox, the backend and the web app from this one window, then watches them. If one stops
  or stops answering, it starts it again within about 15 seconds. While this window is open, Windows will
  not sleep or turn the screen off, even on battery. Press Ctrl+C (or close the window) to stop everything.

  Differences from dev.ps1, all on purpose:
    - no --reload, so a file save can't restart or hang the backend during the talk;
    - the servers belong to this window, not to an editor or another tool that may close them;
    - with -EnvFile (default var\deck.env, if present) the demo uses its own copy of the data, so your
      normal var/ databases stay as they are. Without that file it uses the normal var/ data.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1 -Seed          # start from a fresh demo shop
  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1 -EnvFile ""      # use the normal var/ data instead
#>
param(
    [string]$EnvFile = "var\deck.env",
    [int]$SandboxPort = 8100,
    [int]$BackendPort = 8000,
    [int]$WebPort = 5173,
    [switch]$SkipBuild,
    [switch]$Seed   # reset the demo data to the fresh demo shop (clears rehearsal alerts and history)
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Node = (Get-Command node).Source
$Vite = Join-Path $Root "frontend\node_modules\vite\bin\vite.js"
$Logs = Join-Path $Root "var\demo-logs"
New-Item -ItemType Directory -Force $Logs | Out-Null
$Host.UI.RawUI.WindowTitle = "KhataPilot demo (keep this open)"

# ---- refuse to start on top of servers that are already running ----
$busy = @()
foreach ($port in $SandboxPort, $BackendPort, $WebPort) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)"
        $busy += "  port $port is used by $($p.Name) (PID $($p.ProcessId)), started by $($parent.Name)"
    }
}
if ($busy) {
    Write-Host "Something is already running on the demo ports:" -ForegroundColor Yellow
    $busy | ForEach-Object { Write-Host $_ }
    Write-Host "Close those windows (or stop them in the app that started them), then run this again."
    exit 1
}

# ---- keep the PC awake while this window is open (undone automatically when it closes) ----
Add-Type -Namespace KhataPilot -Name Power -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
"@
$ES_CONTINUOUS = [uint32]"0x80000000"; $ES_SYSTEM = [uint32]1; $ES_DISPLAY = [uint32]2
[void][KhataPilot.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM -bor $ES_DISPLAY)

# ---- the three services ----
$envArgs = @()
if ($EnvFile -and -not (Test-Path $EnvFile)) {
    if ($PSBoundParameters.ContainsKey("EnvFile")) { throw "Env file $EnvFile not found" }
    Write-Host "No $EnvFile here; using the normal var/ data." -ForegroundColor Yellow
    $EnvFile = ""
}
if ($EnvFile) {
    $envArgs = @("--env-file", $EnvFile)
    # Also load it into this window, so seed.py writes to the same data folder as the servers.
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2].Trim() }
    }
}
# One maths thread each: OpenBLAS otherwise reserves a buffer per CPU core, and on a PC that is short of
# memory the backend can die at start with "OpenBLAS error: Memory allocation still failed".
$env:OPENBLAS_NUM_THREADS = "1"; $env:OMP_NUM_THREADS = "1"; $env:MKL_NUM_THREADS = "1"
$env:SANDBOX_URL = "http://127.0.0.1:$SandboxPort"
$env:BACKEND_URL = "http://127.0.0.1:$BackendPort"

$services = @(
    @{ Name = "sandbox"; File = $Py; Dir = $Root; Health = "http://127.0.0.1:$SandboxPort/health"
       Args = @("-m", "uvicorn", "sandbox.app:create_app", "--factory", "--port", "$SandboxPort") + $envArgs },
    @{ Name = "backend"; File = $Py; Dir = $Root; Health = "http://127.0.0.1:$BackendPort/api/health"
       Args = @("-m", "uvicorn", "app.main:create_app", "--factory", "--app-dir", "backend",
                "--port", "$BackendPort") + $envArgs },
    @{ Name = "web"; File = $Node; Dir = (Join-Path $Root "frontend"); Health = "http://localhost:$WebPort/"
       Args = @("`"$Vite`"", "preview", "--port", "$WebPort", "--strictPort") }
)

function Stop-Service2($svc) {
    if ($svc.Proc -and -not $svc.Proc.HasExited) {
        # /T: the venv's python.exe is a launcher; the real server is its child.
        & taskkill /T /F /PID $svc.Proc.Id 2>$null | Out-Null
    }
    $svc.Proc = $null
}

function Start-Service2($svc) {
    $stamp = Get-Date -Format "HHmmss"
    $svc.Proc = Start-Process -FilePath $svc.File -ArgumentList $svc.Args -WorkingDirectory $svc.Dir `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "$($svc.Name)-$stamp.out.log") `
        -RedirectStandardError (Join-Path $Logs "$($svc.Name)-$stamp.err.log")
    $svc.Fails = 0
    $svc.Since = Get-Date
}

function Test-Up($svc) {
    try { Invoke-WebRequest -Uri $svc.Health -UseBasicParsing -TimeoutSec 4 | Out-Null; return $true }
    catch { return $false }
}

function Wait-Up($svc) {
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Up $svc) { Write-Host ("  {0,-8} up    {1}" -f $svc.Name, $svc.Health) -ForegroundColor Green; return }
        if ($svc.Proc.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  $($svc.Name) did not come up; see the newest $($svc.Name)-*.err.log in $Logs" -ForegroundColor Red
}

try {
    if (-not $SkipBuild) {
        Write-Host "Building the web app once..." -ForegroundColor Cyan
        Push-Location frontend
        & $Node $Vite build --logLevel warn
        if ($LASTEXITCODE -ne 0) { throw "Web app build failed" }
        Pop-Location
    }
    if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) { throw "frontend\dist is missing; run without -SkipBuild" }

    Write-Host "Starting..." -ForegroundColor Cyan
    foreach ($svc in $services) {
        Start-Service2 $svc; Wait-Up $svc
        if ($Seed -and $svc.Name -eq "sandbox") {
            # After the sandbox is up (seed.py loads it too) and before the backend opens the database.
            Write-Host "Resetting the demo data..." -ForegroundColor Cyan
            & $Py scripts\seed.py | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Seeding failed (run: python scripts\seed.py to see why)" }
            Write-Host "  demo shop loaded fresh" -ForegroundColor Green
        }
    }

    Write-Host ""
    Write-Host "KhataPilot is running at http://localhost:$WebPort" -ForegroundColor Cyan
    Write-Host "Data: $(if ($EnvFile) { $EnvFile } else { 'var/ (normal)' })   Logs: $Logs"
    Write-Host "The PC won't sleep while this window is open. Ctrl+C stops everything."
    Write-Host ""

    while ($true) {
        Start-Sleep -Seconds 5
        foreach ($svc in $services) {
            $why = $null
            if ($svc.Proc.HasExited) {
                $why = "stopped (exit code $($svc.Proc.ExitCode))"
            } elseif (-not (Test-Up $svc)) {
                $svc.Fails++
                # Three missed checks in a row (about 15 s plus timeouts) means it is hung, not just busy.
                if ($svc.Fails -ge 3) { $why = "not answering" }
            } else {
                $svc.Fails = 0
            }
            if ($why) {
                Write-Host ("{0}  {1} {2}; restarting" -f (Get-Date -Format "HH:mm:ss"), $svc.Name, $why) -ForegroundColor Yellow
                Stop-Service2 $svc
                Start-Service2 $svc
                Wait-Up $svc
            }
        }
    }
}
finally {
    Write-Host "Stopping..." -ForegroundColor Cyan
    foreach ($svc in $services) { Stop-Service2 $svc }
    [void][KhataPilot.Power]::SetThreadExecutionState($ES_CONTINUOUS)
}
