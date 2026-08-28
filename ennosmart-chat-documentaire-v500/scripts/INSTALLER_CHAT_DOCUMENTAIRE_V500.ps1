$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Installer = Join-Path $PSScriptRoot "install_chat_documentaire_v500.py"

Write-Host "=== EnnoSmart - Chat documentaire V5.00 ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Installer

if ($LASTEXITCODE -ne 0) {
    throw "Installation Chat documentaire V5.00 echouee."
}

Write-Host ""
Write-Host "Chat documentaire V5.00 installe." -ForegroundColor Green
Write-Host "Actualise le frontend." -ForegroundColor Yellow
