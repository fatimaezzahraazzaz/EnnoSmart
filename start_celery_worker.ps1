$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend_api"
$pythonCandidates = @(
    (Join-Path $projectRoot ".venv_py314\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\Scripts\python.exe")
)
$pythonExe = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $pythonExe) {
    throw "Environnement Python introuvable. Chemins verifies : $($pythonCandidates -join ', ')"
}

Set-Location $backendRoot

# Lanceur compatible Windows : il prépare lui-même les chemins Python du
# backend et évite les erreurs tardives « No module named db ».
& $pythonExe `
    (Join-Path $backendRoot "worker\run_local_worker.py") `
    "ennoscholar@%h" `
    "celery"
