[CmdletBinding()]
param(
    [ValidateSet("scan", "apply", "status")]
    [string]$Action = "scan",
    [int]$MaxScopes = 0,
    [switch]$IncludeProbable
)

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv_py314\Scripts\python.exe"
$automation = Join-Path $PSScriptRoot "automate_cir_memory.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python du projet introuvable : $python"
}

if ($Action -eq "scan") {
    $arguments = @($automation, "all")
    if ($MaxScopes -gt 0) {
        $arguments += @("--max-scopes", [string]$MaxScopes)
    }
    if ($IncludeProbable) {
        $arguments += "--include-probable"
    }
    & $python @arguments
} elseif ($Action -eq "apply") {
    & $python $automation all --apply-latest --confirm INDEXER_TOUT_LE_CORPUS
} else {
    & $python $automation status
}

exit $LASTEXITCODE
