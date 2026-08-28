$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Installer = Join-Path $PSScriptRoot "install_cir_memory_tree_v600.py"

Write-Host "=== CIR Memory - Arborescence V6.00 ===" -ForegroundColor Cyan

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

python $Installer

if ($LASTEXITCODE -ne 0) {
    throw "Installation CIR Memory V6.00 echouee."
}

Write-Host ""
Write-Host "CIR Memory V6.00 installee." -ForegroundColor Green
Write-Host "Redemarre FastAPI puis actualise le frontend." -ForegroundColor Yellow
