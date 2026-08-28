$ErrorActionPreference = "Stop"

Write-Host "=== TEST LIBREOFFICE ENNOSMART V171 ===" -ForegroundColor Cyan

$Repo = "C:\EnnoSmart"
Set-Location $Repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

$Soffice = @(
    $env:LIBREOFFICE_BIN,
    "C:\Program Files\LibreOffice\program\soffice.exe",
    "C:\Program Files\LibreOffice\program\soffice.com",
    "C:\Program Files (x86)\LibreOffice\program\soffice.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $Soffice) {
    Write-Host "LibreOffice introuvable." -ForegroundColor Red
    exit 1
}

Write-Host "LibreOffice :" $Soffice -ForegroundColor Green
& $Soffice --version

# Neutraliser les variables Python qui peuvent perturber LibreOffice.
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONSTARTUP -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONUSERBASE -ErrorAction SilentlyContinue

$Doc = Get-ChildItem "C:\EnnoSmart\storage" -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -like "PV-CV 23-0190_Essais-V875-RT*" -and
        $_.Extension -in ".docx", ".doc", ".docm"
    } |
    Select-Object -First 1

if (-not $Doc) {
    Write-Host "Document de test non trouvé automatiquement." -ForegroundColor Yellow
    Write-Host "Le backend V171 peut quand même être utilisé."
    exit 0
}

Write-Host "Document :" $Doc.FullName -ForegroundColor Green

$TestRoot = Join-Path $env:TEMP ("ennosmart_lo_manual_" + [guid]::NewGuid().ToString("N"))
$InputDir = Join-Path $TestRoot "input"
$OutputDir = Join-Path $TestRoot "output"
$ProfileDir = Join-Path $TestRoot "profile"

New-Item -ItemType Directory -Force $InputDir | Out-Null
New-Item -ItemType Directory -Force $OutputDir | Out-Null
New-Item -ItemType Directory -Force $ProfileDir | Out-Null

$TempDoc = Join-Path $InputDir $Doc.Name
Copy-Item $Doc.FullName $TempDoc -Force

$ProfileUri = (New-Object System.Uri($ProfileDir)).AbsoluteUri

Write-Host ""
Write-Host "Conversion directe..." -ForegroundColor Yellow

& $Soffice `
    "-env:UserInstallation=$ProfileUri" `
    --headless `
    --nologo `
    --nodefault `
    --nolockcheck `
    --nofirststartwizard `
    --convert-to pdf `
    --outdir $OutputDir `
    $TempDoc

Write-Host ""
Get-ChildItem $OutputDir -Force

$Pdf = Get-ChildItem $OutputDir -Filter "*.pdf" -File | Select-Object -First 1

if ($Pdf) {
    Write-Host ""
    Write-Host "SUCCES : PDF produit" -ForegroundColor Green
    Write-Host $Pdf.FullName
    Write-Host "Taille :" $Pdf.Length "octets"
} else {
    Write-Host ""
    Write-Host "ECHEC : aucun PDF produit." -ForegroundColor Red
}
