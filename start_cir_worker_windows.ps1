$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot

$env:PYTHONPATH = "$projectRoot;$projectRoot\backend_api"

# Le .env principal conserve le broker historique d'EnnoScholar sur Redis /0.
# Ce processus est dédié au CIR : on verrouille donc explicitement son broker
# et son backend afin qu'une variable héritée ne détourne pas le worker.
$env:CELERY_BROKER_URL = "redis://127.0.0.1:6379/1"
$env:CELERY_RESULT_BACKEND = "redis://127.0.0.1:6379/2"

# V3.21.4 : transport CIR lu depuis .ennosmart-cir-runtime.env

$workerConcurrency = if ($env:ENNOSMART_CIR_WORKER_CONCURRENCY) {
    $env:ENNOSMART_CIR_WORKER_CONCURRENCY
} else {
    "4"
}

Write-Host "[EnnoSmart] Celery worker CIR - Windows DEV / pool=threads / concurrency=$workerConcurrency"
Write-Host "[EnnoSmart] Queue = ennosmart.cir"

$pythonCandidates = @(
    (Join-Path $projectRoot ".venv_py314\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\Scripts\python.exe")
)
$pythonExe = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExe) {
    throw "Interpréteur Python du projet introuvable. Chemins verifies : $($pythonCandidates -join ', ')"
}

& $pythonExe -m celery `
    -A backend_api.workers.celery_app:celery_app `
    worker `
    --loglevel=INFO `
    --pool=threads `
    --concurrency=$workerConcurrency `
    -Q ennosmart.cir
