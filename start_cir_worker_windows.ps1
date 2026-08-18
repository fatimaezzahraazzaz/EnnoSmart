$ErrorActionPreference = "Stop"

$env:PYTHONPATH = "C:\EnnoSmart;C:\EnnoSmart\backend_api"

# Le .env principal conserve le broker historique d'EnnoScholar sur Redis /0.
# Ce processus est dédié au CIR : on verrouille donc explicitement son broker
# et son backend afin qu'une variable héritée ne détourne pas le worker.
$env:CELERY_BROKER_URL = "redis://127.0.0.1:6379/1"
$env:CELERY_RESULT_BACKEND = "redis://127.0.0.1:6379/2"

# V3.21.4 : transport CIR lu depuis .ennosmart-cir-runtime.env

Write-Host "[EnnoSmart] Celery worker CIR - Windows DEV / pool=solo"
Write-Host "[EnnoSmart] Queue = ennosmart.cir"

$pythonExe = "C:\EnnoSmart\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Interpréteur Python du projet introuvable : $pythonExe"
}

& $pythonExe -m celery `
    -A backend_api.workers.celery_app:celery_app `
    worker `
    --loglevel=INFO `
    --pool=solo `
    --concurrency=1 `
    -Q ennosmart.cir
