$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Updater = Join-Path $PSScriptRoot "update_agent3_pdf_comparison_v402.py"

Write-Host "=== EnnoSmart Agent 3 - Comparatif V4.02 ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Updater

if ($LASTEXITCODE -ne 0) {
    throw "Correctif V4.02 echoue."
}

Write-Host ""
Write-Host "V4.02 installee." -ForegroundColor Green
Write-Host "Redemarre FastAPI puis actualise le navigateur." -ForegroundColor Yellow
