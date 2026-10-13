param(
    [string]$EnnoSmartRoot = ""
)

$ErrorActionPreference = "Stop"
if (-not $EnnoSmartRoot) {
    $EnnoSmartRoot = $PSScriptRoot
}
$ServerRoot = Join-Path $EnnoSmartRoot "mcp_servers\legal_fulltext_mcp"
$Python = Join-Path $EnnoSmartRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Environnement MCP introuvable : $Python"
}

Set-Location $EnnoSmartRoot
Write-Host "[EnnoScholar MCP] Python: $Python"
Write-Host "[EnnoScholar MCP] REST: http://127.0.0.1:8010/api/resolve"
& $Python -m mcp_servers.legal_fulltext_mcp.server
