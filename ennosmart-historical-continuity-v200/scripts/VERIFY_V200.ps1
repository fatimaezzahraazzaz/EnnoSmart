param(
    [string]$RepoRoot = "C:\EnnoSmart"
)

$ErrorActionPreference = "Stop"
$PackRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) { $Python = "python" }

$Agent = Join-Path $RepoRoot "agents\EnnoDiagnostic\ennodiagnostic_agent.py"
$Module = Join-Path $RepoRoot "agents\EnnoDiagnostic\historical_continuity_reconciler.py"

Write-Host "[1/3] Compile V200 module" -ForegroundColor Cyan
& $Python -m py_compile $Module
if ($LASTEXITCODE -ne 0) { throw "Module compile failed." }

Write-Host "[2/3] Compile EnnoDiagnostic agent" -ForegroundColor Cyan
& $Python -m py_compile $Agent
if ($LASTEXITCODE -ne 0) { throw "Agent compile failed." }

Write-Host "[3/3] Run V200 unit tests" -ForegroundColor Cyan
Push-Location $PackRoot
try {
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }
} finally {
    Pop-Location
}

Write-Host "V200 verification OK." -ForegroundColor Green
