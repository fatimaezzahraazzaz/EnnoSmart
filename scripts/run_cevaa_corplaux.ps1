[CmdletBinding()]
param(
    [ValidateSet("scan", "apply", "status")]
    [string]$Action = "scan"
)

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv_py314\Scripts\python.exe"
$automation = Join-Path $PSScriptRoot "automate_cir_memory.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python du projet introuvable : $python"
}

if ($Action -eq "scan") {
    & $python $automation pilot
} elseif ($Action -eq "apply") {
    & $python $automation pilot --apply-latest --confirm INDEXER_CORPLAUX
} else {
    & $python $automation status
}

exit $LASTEXITCODE
