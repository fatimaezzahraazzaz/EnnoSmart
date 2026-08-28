$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$PackRoot = Split-Path -Parent $PSScriptRoot

$FrontendSource = Join-Path $PackRoot "frontend\components\ennosmart\diagnosis-page.tsx"
$BackendSource  = Join-Path $PackRoot "backend_api\routers\source_highlight.py"

$FrontendTarget = Join-Path $Repo "frontend\components\ennosmart\diagnosis-page.tsx"
$BackendTarget  = Join-Path $Repo "backend_api\routers\source_highlight.py"

Write-Host "=== EnnoSmart V172 — auto-scroll comparaison A/B ===" -ForegroundColor Cyan

Copy-Item $FrontendTarget "$FrontendTarget.before-v172" -Force
Copy-Item $BackendTarget  "$BackendTarget.before-v172" -Force

Copy-Item $FrontendSource $FrontendTarget -Force
Copy-Item $BackendSource  $BackendTarget -Force

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python -m py_compile ".\backend_api\routers\source_highlight.py"

if ($LASTEXITCODE -ne 0) {
    throw "Erreur Python dans source_highlight.py"
}

Write-Host ""
Write-Host "V172 installée." -ForegroundColor Green
Write-Host "Redémarre le backend puis actualise le frontend." -ForegroundColor Yellow
Write-Host "Quand tu sélectionnes un passage, A et B s'ouvriront automatiquement à la page correspondante." -ForegroundColor Cyan
