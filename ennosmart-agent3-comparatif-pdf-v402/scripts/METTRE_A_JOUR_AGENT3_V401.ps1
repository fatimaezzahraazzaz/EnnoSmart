$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Updater = Join-Path $PSScriptRoot "update_agent3_pdf_comparison_v401.py"

Write-Host "=== EnnoSmart Agent 3 - Correctif PDF V4.01 ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Updater

if ($LASTEXITCODE -ne 0) {
    throw "Correctif V4.01 echoue."
}

Write-Host ""
Write-Host "Correctif V4.01 installe." -ForegroundColor Green
Write-Host "Redemarre FastAPI et actualise le navigateur." -ForegroundColor Yellow
