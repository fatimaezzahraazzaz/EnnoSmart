param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8002,
    [string]$BindAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

$PythonCandidates = @()
if ($env:VIRTUAL_ENV) {
    $PythonCandidates += Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
}
$PythonCandidates += @(
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot ".venv_py314\Scripts\python.exe")
)

$Python = $null
foreach ($Candidate in ($PythonCandidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        continue
    }
    & $Candidate -c "import uvicorn" *> $null
    if ($LASTEXITCODE -eq 0) {
        $Python = $Candidate
        break
    }
}

if (-not $Python) {
    throw "Environnement Python EnnoSmart introuvable ou incomplet. Exécutez d'abord : py -3.12 -m venv .venv puis python -m pip install -r requirements.txt"
}

Set-Location $ProjectRoot
Write-Host "[EnnoSmart Backend] Python: $Python"
Write-Host "[EnnoSmart Backend] API: http://${BindAddress}:$Port"

& $Python -m uvicorn main:app `
    --app-dir (Join-Path $ProjectRoot "backend_api") `
    --host $BindAddress `
    --port $Port
