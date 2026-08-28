$ErrorActionPreference = "Continue"

$File = "C:\EnnoSmart\backend_api\services\diagnostic_service.py"

Write-Host "=== Diagnostic fichier diagnostic_service.py ===" -ForegroundColor Cyan
Write-Host "Existe : $(Test-Path $File)"

if (Test-Path $File) {
    Get-Item $File | Format-List FullName,Length,Attributes,Mode,LastWriteTime

    Write-Host ""
    Write-Host "Processus Python / Uvicorn / Celery actifs :" -ForegroundColor Yellow
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match "python|uvicorn|celery"
        } |
        Select-Object ProcessId,Name,CommandLine |
        Format-Table -AutoSize
}
