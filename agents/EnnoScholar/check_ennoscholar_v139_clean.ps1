param(
    [string]$Package = "C:\EnnoSmart\agents\EnnoScholar",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
& $Python (Join-Path $Package "check_ennoscholar_complete.py") --package $Package
if ($LASTEXITCODE -ne 0) {
    throw "Les contrôles EnnoScholar ont échoué."
}
