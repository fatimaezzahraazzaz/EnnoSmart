$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Patcher = Join-Path $PSScriptRoot "patch_login_direct_reset_v1.py"

Write-Host "=== Login - Mot de passe oublie direct DEV ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Patcher

if ($LASTEXITCODE -ne 0) {
    throw "Correction login echouee."
}

Write-Host ""
Write-Host "Login corrige." -ForegroundColor Green
Write-Host "Actualise le frontend." -ForegroundColor Yellow
