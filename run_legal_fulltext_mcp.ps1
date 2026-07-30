param(
    [string]$EnnoSmartRoot = "C:\EnnoSmart"
)

$ErrorActionPreference = "Stop"
$ServerRoot = Join-Path $EnnoSmartRoot "mcp_servers\legal_fulltext_mcp"
$Python = Join-Path $ServerRoot ".venv_mcp\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Environnement MCP introuvable : $Python. Lance d'abord install_v1_7_free_discovery.ps1."
}

Set-Location $EnnoSmartRoot
& $Python -m mcp_servers.legal_fulltext_mcp.server
