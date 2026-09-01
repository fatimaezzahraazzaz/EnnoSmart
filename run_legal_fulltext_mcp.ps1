param(
    [string]$EnnoSmartRoot = ""
)

$ErrorActionPreference = "Stop"
if (-not $EnnoSmartRoot) {
    $EnnoSmartRoot = $PSScriptRoot
}
$ServerRoot = Join-Path $EnnoSmartRoot "mcp_servers\legal_fulltext_mcp"
$PythonCandidates = @(
    (Join-Path $EnnoSmartRoot ".venv-mcp\Scripts\python.exe"),
    (Join-Path $ServerRoot ".venv_mcp\Scripts\python.exe"),
    (Join-Path $EnnoSmartRoot ".venv_py314\Scripts\python.exe"),
    (Join-Path $EnnoSmartRoot ".venv\Scripts\python.exe")
)
$Python = $PythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Python) {
    throw "Environnement MCP introuvable. Chemins verifies : $($PythonCandidates -join ', ')"
}

Set-Location $EnnoSmartRoot
Write-Host "[EnnoScholar MCP] Python: $Python"
Write-Host "[EnnoScholar MCP] REST: http://127.0.0.1:8010/api/resolve"
& $Python -m mcp_servers.legal_fulltext_mcp.server
