$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$PackBackend = Join-Path $PSScriptRoot "..\backend_api\routers\source_highlight.py"
$TargetBackend = Join-Path $Repo "backend_api\routers\source_highlight.py"
$Backup = Join-Path $Repo "backend_api\routers\source_highlight.py.before-v171"

Write-Host "=== INSTALLATION SOURCE HIGHLIGHT V171 ===" -ForegroundColor Cyan

if (!(Test-Path $PackBackend)) {
    throw "Fichier pack introuvable : $PackBackend"
}

if (Test-Path $TargetBackend) {
    Copy-Item $TargetBackend $Backup -Force
    Write-Host "Backup :" $Backup -ForegroundColor Yellow
}

Copy-Item $PackBackend $TargetBackend -Force

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python -m py_compile ".\backend_api\routers\source_highlight.py"

if ($LASTEXITCODE -ne 0) {
    throw "Erreur syntaxe Python."
}

# Nettoyer uniquement les caches de conversion Office ratés.
$OfficeCache = Join-Path $Repo "storage\previews\source_highlight\office_pdf"

if (Test-Path $OfficeCache) {
    Write-Host "Suppression du cache Office précédent :" $OfficeCache -ForegroundColor Yellow
    Remove-Item $OfficeCache -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "V171 installé avec succès." -ForegroundColor Green
Write-Host "Redémarre maintenant le backend FastAPI." -ForegroundColor Cyan
