$ErrorActionPreference = "Stop"

Write-Host "=== EnnoSmart Office faithful preview ===" -ForegroundColor Cyan

$repo = "C:\EnnoSmart"
Set-Location $repo

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
}

Write-Host ""
Write-Host "1) Vérification Python..." -ForegroundColor Yellow
python -m py_compile ".\backend_api\routers\source_highlight.py"

Write-Host ""
Write-Host "2) Vérification PyMuPDF..." -ForegroundColor Yellow
python -c "import fitz; print('PyMuPDF OK:', fitz.__doc__.splitlines()[0])"

Write-Host ""
Write-Host "3) Recherche LibreOffice..." -ForegroundColor Yellow

$candidates = @(
    "C:\Program Files\LibreOffice\program\soffice.exe",
    "C:\Program Files (x86)\LibreOffice\program\soffice.exe"
)

$found = $null
foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
        $found = $candidate
        break
    }
}

if ($found) {
    Write-Host "LibreOffice trouvé : $found" -ForegroundColor Green
    $env:LIBREOFFICE_BIN = $found
    Write-Host ""
    Write-Host "Ajoute éventuellement dans ton .env :" -ForegroundColor Yellow
    Write-Host "LIBREOFFICE_BIN=$found"
} else {
    Write-Host "LibreOffice non trouvé." -ForegroundColor Red
    Write-Host "Installe LibreOffice puis relance ce script."
}

Write-Host ""
Write-Host "Après remplacement des fichiers : redémarre FastAPI et Next.js." -ForegroundColor Cyan
