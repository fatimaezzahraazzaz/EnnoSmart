$ErrorActionPreference = "Stop"

# Alias de compatibilite pour la commande historique utilisee en local.
# Le serveur MCP reste implemente et configure par le lanceur canonique.
$projectRoot = $PSScriptRoot
$mcpLauncher = Join-Path $projectRoot "run_legal_fulltext_mcp.ps1"

if (-not (Test-Path -LiteralPath $mcpLauncher)) {
    throw "Lanceur MCP introuvable : $mcpLauncher"
}

& $mcpLauncher -EnnoSmartRoot $projectRoot
exit $LASTEXITCODE
