$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend_api"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Environnement Python introuvable : $pythonExe"
}

Set-Location $backendRoot

# Lanceur compatible Windows : il prépare lui-même les chemins Python du
# backend et évite les erreurs tardives « No module named db ».
& $pythonExe `
    (Join-Path $backendRoot "worker\run_local_worker.py") `
    "ennoscholar@%h" `
    "celery"
