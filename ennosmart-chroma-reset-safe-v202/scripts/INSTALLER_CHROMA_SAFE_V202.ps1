$ErrorActionPreference = "Stop"

$Repo = "C:\EnnoSmart"
$Patcher = Join-Path $PSScriptRoot "patch_chroma_reset_safe_v202.py"

Write-Host "=== EnnoSmart Chroma reset safe V202 ===" -ForegroundColor Cyan
Write-Host "Ecriture temporaire + remplacement atomique Windows." -ForegroundColor DarkGray

Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

$Target = "C:\EnnoSmart\backend_api\services\diagnostic_service.py"
if (Test-Path $Target) {
    try { attrib -R $Target 2>$null } catch {}
}

python $Patcher

if ($LASTEXITCODE -ne 0) {
    throw "Correctif Chroma V202 echoue."
}

Write-Host ""
Write-Host "V202 installee." -ForegroundColor Green
Write-Host "Redemarre FastAPI puis relance Preparer les sources." -ForegroundColor Yellow
