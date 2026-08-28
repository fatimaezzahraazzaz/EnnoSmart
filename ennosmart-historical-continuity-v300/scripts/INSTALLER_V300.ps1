$ErrorActionPreference = "Stop"
cd C:\EnnoSmart
if (Test-Path ".\.venv\Scripts\Activate.ps1") { & ".\.venv\Scripts\Activate.ps1" }
python "$PSScriptRoot\apply_v300_patch.py" --repo C:\EnnoSmart
if ($LASTEXITCODE -ne 0) { throw "Installation V300 echouee" }
Write-Host "V300 installee" -ForegroundColor Green
