$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Updater = Join-Path $PSScriptRoot "update_agent3_after_decision_v406.py"

Write-Host "=== EnnoSmart Agent 3 - Etat apres decision V4.06 ===" -ForegroundColor Cyan
Set-Location $Repo
if (Test-Path ".\.venv\Scripts\Activate.ps1") { & ".\.venv\Scripts\Activate.ps1" }
python $Updater
if ($LASTEXITCODE -ne 0) { throw "Correctif V4.06 echoue." }
Write-Host ""
Write-Host "V4.06 installee." -ForegroundColor Green
Write-Host "Redemarre FastAPI puis actualise le frontend." -ForegroundColor Yellow
