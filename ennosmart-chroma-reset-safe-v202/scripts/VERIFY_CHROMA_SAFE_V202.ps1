$ErrorActionPreference = "Stop"
$File = "C:\EnnoSmart\backend_api\services\diagnostic_service.py"

Write-Host "=== Verification Chroma V202 ===" -ForegroundColor Cyan

if (!(Test-Path $File)) {
    throw "diagnostic_service.py introuvable."
}

$Text = Get-Content $File -Raw

$Checks = @(
    @("version V202", $Text.Contains("diagnostic_generated_artifacts_reset_v2_chroma_safe")),
    @("chunks.json regenere", $Text.Contains('Path(project_store.rag_dir) / "chunks.json"')),
    @("chroma preserve", $Text.Contains("chroma_filesystem_deleted")),
    @("reset collection differe", $Text.Contains("chroma_collection_reset_later"))
)

$Failed = $false
foreach ($Item in $Checks) {
    if ($Item[1]) {
        Write-Host "[OK] $($Item[0])" -ForegroundColor Green
    } else {
        Write-Host "[ERREUR] $($Item[0])" -ForegroundColor Red
        $Failed = $true
    }
}

if ($Failed) {
    throw "Verification V202 echouee."
}

Write-Host ""
Write-Host "V202 OK." -ForegroundColor Green
