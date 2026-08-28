param(
    [string]$RepoRoot = "C:\EnnoSmart"
)

$ErrorActionPreference = "Stop"

$PackRoot = Split-Path -Parent $PSScriptRoot
$ModuleSource = Join-Path $PackRoot "agents\EnnoDiagnostic\historical_continuity_reconciler.py"
$ModuleTargetDir = Join-Path $RepoRoot "agents\EnnoDiagnostic"
$ModuleTarget = Join-Path $ModuleTargetDir "historical_continuity_reconciler.py"
$PatchScript = Join-Path $PSScriptRoot "apply_v200_patch.py"
$AgentTarget = Join-Path $ModuleTargetDir "ennodiagnostic_agent.py"

Write-Host "[V200] EnnoDiagnostic historical continuity installer" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"

if (!(Test-Path $RepoRoot)) {
    throw "Repo root not found: $RepoRoot"
}
if (!(Test-Path $AgentTarget)) {
    throw "EnnoDiagnostic agent not found: $AgentTarget"
}
if (!(Test-Path $ModuleSource)) {
    throw "V200 module not found in pack: $ModuleSource"
}
if (!(Test-Path $PatchScript)) {
    throw "V200 patch script not found: $PatchScript"
}

New-Item -ItemType Directory -Force -Path $ModuleTargetDir | Out-Null

if (Test-Path $ModuleTarget) {
    $ModuleBackup = "$ModuleTarget.before-v200"
    if (!(Test-Path $ModuleBackup)) {
        Copy-Item $ModuleTarget $ModuleBackup -Force
        Write-Host "Backup created: $ModuleBackup" -ForegroundColor DarkGray
    }
}

Copy-Item $ModuleSource $ModuleTarget -Force
Write-Host "V200 reconciler copied." -ForegroundColor Green

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) {
    $Python = "python"
}

& $Python $PatchScript --repo $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw "V200 patch failed. The original agent backup is kept as ennodiagnostic_agent.py.before-v200"
}

& $Python -m py_compile $ModuleTarget
if ($LASTEXITCODE -ne 0) { throw "Python compile failed for historical_continuity_reconciler.py" }

& $Python -m py_compile $AgentTarget
if ($LASTEXITCODE -ne 0) { throw "Python compile failed for ennodiagnostic_agent.py" }

Write-Host "" 
Write-Host "V200 INSTALLED SUCCESSFULLY" -ForegroundColor Green
Write-Host "Restart the EnnoSmart backend, then relaunch EnnoDiagnostic." -ForegroundColor Yellow
Write-Host "The longitudinal report will be written under the project ennodiagnostic folder as historical_continuity_report_v200.json." -ForegroundColor Yellow
