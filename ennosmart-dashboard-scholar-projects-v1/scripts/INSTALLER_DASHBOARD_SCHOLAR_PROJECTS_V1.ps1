$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Patcher = Join-Path $PSScriptRoot "patch_dashboard_scholar_projects_v1.py"

Write-Host "=== Dashboard - EnnoScholar par projets ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Patcher

if ($LASTEXITCODE -ne 0) {
    throw "Correction dashboard echouee."
}

Write-Host ""
Write-Host "Dashboard corrige." -ForegroundColor Green
Write-Host "Actualise le frontend." -ForegroundColor Yellow
