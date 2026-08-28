$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Updater = Join-Path $PSScriptRoot "update_agent3_compact_actions_v404.py"

Write-Host "=== EnnoSmart Agent 3 - Actions compactes V4.04 ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Updater

if ($LASTEXITCODE -ne 0) {
    throw "Correctif V4.04 echoue."
}

Write-Host ""
Write-Host "V4.04 installee." -ForegroundColor Green
Write-Host "Actualise le frontend." -ForegroundColor Yellow
