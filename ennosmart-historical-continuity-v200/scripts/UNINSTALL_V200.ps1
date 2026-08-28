param(
    [string]$RepoRoot = "C:\EnnoSmart"
)

$ErrorActionPreference = "Stop"
$Agent = Join-Path $RepoRoot "agents\EnnoDiagnostic\ennodiagnostic_agent.py"
$Backup = "$Agent.before-v200"
$Module = Join-Path $RepoRoot "agents\EnnoDiagnostic\historical_continuity_reconciler.py"
$ModuleBackup = "$Module.before-v200"

if (!(Test-Path $Backup)) {
    throw "Backup not found: $Backup"
}

Copy-Item $Backup $Agent -Force

if (Test-Path $ModuleBackup) {
    Copy-Item $ModuleBackup $Module -Force
} elseif (Test-Path $Module) {
    Remove-Item $Module -Force
}

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) { $Python = "python" }
& $Python -m py_compile $Agent
if ($LASTEXITCODE -ne 0) { throw "Restored agent does not compile." }

Write-Host "V200 removed. Previous EnnoDiagnostic agent restored." -ForegroundColor Green
