$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Patcher = Join-Path $PSScriptRoot "patch_chroma_reset_safe_v201.py"

Write-Host "=== EnnoSmart Chroma reset safe V201 ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Patcher

if ($LASTEXITCODE -ne 0) {
    throw "Correctif Chroma V201 echoue."
}

Write-Host ""
Write-Host "V201 installee." -ForegroundColor Green
Write-Host "Redemarre le backend FastAPI puis relance Preparer les sources." -ForegroundColor Yellow
