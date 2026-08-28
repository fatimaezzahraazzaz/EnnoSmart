$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Installer = Join-Path $PSScriptRoot "install_agent3_pdf_comparison_v400.py"

Write-Host "=== EnnoSmart Agent 3 - Comparatif PDF V4.00 ===" -ForegroundColor Cyan

if (!(Test-Path $Repo)) {
    throw "Repo introuvable: $Repo"
}

if (!(Test-Path $Installer)) {
    throw "Installer Python introuvable: $Installer"
}

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Installer

if ($LASTEXITCODE -ne 0) {
    throw "Installation Agent 3 echouee."
}

Write-Host ""
Write-Host "Agent 3 V4.00 installe." -ForegroundColor Green
Write-Host "Redemarre le backend FastAPI et actualise le frontend." -ForegroundColor Yellow
