param(
    [string]$Root = "C:\EnnoSmart",
    [int]$AnrProjects = 6000,
    [int]$HalDocs = 1500,
    [int]$HalPdfs = 0
)

$ErrorActionPreference = "Stop"

Set-Location $Root

if (Test-Path "$Root\.venv\Scripts\Activate.ps1") {
    & "$Root\.venv\Scripts\Activate.ps1"
}

$Builder = "$Root\train\dataset_v2_builder"

Write-Host "============================================================"
Write-Host "EnnoSmart - Construction Dataset V2"
Write-Host "============================================================"

python "$Builder\collect_anr.py" `
    --root $Root `
    --max-projects $AnrProjects

python "$Builder\collect_hal.py" `
    --root $Root `
    --max-docs $HalDocs `
    --download-pdfs $HalPdfs

python "$Builder\build_candidates.py" `
    --root $Root

Write-Host ""
Write-Host "OK - Ouvre maintenant :"
Write-Host "$Root\train\data_v2\candidates\fastjudge_review.csv"
Write-Host "$Root\train\data_v2\candidates\verrou_review.csv"
Write-Host ""
Write-Host "Ne lance finalize_splits.py qu'apres une premiere revue humaine."
