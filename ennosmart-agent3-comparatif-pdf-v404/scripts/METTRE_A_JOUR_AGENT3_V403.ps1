$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Updater = Join-Path $PSScriptRoot "update_agent3_comparison_workspace_v403.py"

Write-Host "=== EnnoSmart Agent 3 - Espace Comparatif V4.03 ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Updater

if ($LASTEXITCODE -ne 0) {
    throw "Correctif V4.03 echoue."
}

Write-Host ""
Write-Host "V4.03 installee." -ForegroundColor Green
Write-Host "Actualise le frontend. Aucun rerun Agent 3 n'est necessaire." -ForegroundColor Yellow
