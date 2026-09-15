param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8002,
    [string]$BindAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Environnement Python EnnoSmart introuvable ou incomplet. Exécutez d'abord : py -3.12 -m venv .venv puis python -m pip install -r requirements.txt"
}
& $Python -c "import uvicorn" *> $null
if ($LASTEXITCODE -ne 0) {
    throw "L'environnement .venv existe mais Uvicorn n'est pas installé. Exécutez : .venv\Scripts\python.exe -m pip install -r requirements.txt"
}

Set-Location $ProjectRoot
Write-Host "[EnnoSmart Backend] Python: $Python"
Write-Host "[EnnoSmart Backend] API: http://${BindAddress}:$Port"

& $Python -m uvicorn main:app `
    --app-dir (Join-Path $ProjectRoot "backend_api") `
    --host $BindAddress `
    --port $Port
