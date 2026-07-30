param(
    [string]$Root = "C:\EnnoSmart",

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Organisme,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Project,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Year,

    [string[]]$VerrouAlias = @(),
    [string]$WriterModel = ""
)

$ErrorActionPreference = "Stop"

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$EnvFile = Join-Path $Root ".env"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtuel introuvable : $Python"
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Fichier .env introuvable : $EnvFile"
}

# Charge la configuration générale, sans transformer un alias de projet en
# configuration globale de l'agent.
Get-Content -LiteralPath $EnvFile -Encoding UTF8 | ForEach-Object {
    $Line = $_.Trim()
    if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) {
        return
    }
    $Parts = $Line -split "=", 2
    $Name = $Parts[0].Trim()
    $Value = $Parts[1].Trim()
    if (
        $Value.Length -ge 2 -and
        (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or
         ($Value.StartsWith("'") -and $Value.EndsWith("'")))
    ) {
        $Value = $Value.Substring(1, $Value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    throw "OPENAI_API_KEY est vide dans $EnvFile"
}

$env:ENNOSMART_ROOT = $Root
$env:ENNOSMART_ROOT_DIR = $Root
$env:ENNOSMART_STORAGE_ROOT = Join-Path $Root "storage"
$env:ENNOSMART_STATE_OF_ART_MODE = "global"
$env:ENNOSMART_PHASE5_STATE_OF_ART_MODE = "global"
$env:ENNOSCHOLAR_PHASE5_ENABLE_LLM = "1"
$env:ENNOSCHOLAR_REQUIRE_APPROVED_PLAN = "0"
$env:ENNOSCHOLAR_MEMORY_V2_ENABLED = "0"
$env:ENNOSCHOLAR_MEMORY_V2_TOP_K = "0"

if (-not [string]::IsNullOrWhiteSpace($WriterModel)) {
    $env:ENNOSCHOLAR_PHASE5_WRITER_MODEL = $WriterModel.Trim()
}
if ([string]::IsNullOrWhiteSpace($env:ENNOSCHOLAR_PHASE5_WRITER_MODEL)) {
    throw "Aucun modèle Phase 5 n'est configuré dans le .env ou via -WriterModel."
}

# Un alias doit être fourni par l'appel du projet concerné. La valeur globale
# éventuellement héritée du .env est volontairement ignorée.
Remove-Item Env:ENNOSCHOLAR_VERROU_ALIASES -ErrorAction SilentlyContinue
$AliasArguments = @()
foreach ($Alias in $VerrouAlias) {
    $NormalizedAlias = [string]$Alias
    $NormalizedAlias = $NormalizedAlias.Trim()
    if ($NormalizedAlias -notmatch '^[^=:\s]+\s*(?:=|->|:)\s*[^=:\s]+$') {
        throw "Alias invalide : '$NormalizedAlias'. Format attendu : ancien=canonique"
    }
    $AliasArguments += @("--verrou-alias", $NormalizedAlias)
}

Set-Location -LiteralPath $Root

& $Python -c "from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import run_phase_5_state_of_art_writer; print('IMPORT_PHASE5_OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Échec d'import du nouvel agent EnnoScholar."
}

# Ce chemin temporaire inexistant force le plan scientifique de la Phase 4.7
# pour le test sans frontend, sans dépendre de l'arborescence d'un projet.
$NoChatPlan = Join-Path ([System.IO.Path]::GetTempPath()) (
    "ennoscholar_no_chat_plan_{0}.json" -f [Guid]::NewGuid().ToString("N")
)

$Phase5Arguments = @(
    "-c",
    "from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import main; main()",
    "--organisme",
    $Organisme,
    "--project",
    $Project,
    "--year",
    $Year,
    "--consultant-plan-contract-path",
    $NoChatPlan
) + $AliasArguments

& $Python @Phase5Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Le processus Python de la Phase 5 a échoué."
}

$PathResolver = "import sys; from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import output_payload_path; print(output_payload_path(sys.argv[1], sys.argv[2], sys.argv[3]))"
$ResultPath = & $Python -c $PathResolver $Organisme $Project $Year

if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ResultPath)) {
    throw "Impossible de résoudre le chemin de sortie de la Phase 5."
}
$ResultPath = ([string]($ResultPath | Select-Object -Last 1)).Trim()

if (-not (Test-Path -LiteralPath $ResultPath)) {
    throw "La Phase 5 n'a pas produit son payload : $ResultPath"
}

$Result = Get-Content -LiteralPath $ResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Result.ok) {
    $Message = if ($Result.message) { $Result.message } else { "Erreur métier inconnue" }
    Write-Host ""
    Write-Host "DIAGNOSTIC PHASE 5" -ForegroundColor Yellow
    Write-Host "Status  : $($Result.status)"
    Write-Host "Message : $Message"
    if ($Result.details) {
        Write-Host "Détails :"
        $Result.details | ConvertTo-Json -Depth 20
    }
    if ($Result.input_paths) {
        Write-Host "Entrées :"
        $Result.input_paths | ConvertTo-Json -Depth 20
    }
    throw "Phase 5 refusée : status=$($Result.status) ; message=$Message"
}

Write-Host ""
Write-Host "PHASE5_OK" -ForegroundColor Green
Write-Host "Projet : $Organisme / $Project / $Year"
Write-Host "Writer utilisé : $($Result.writer_used)"
Write-Host "Modèle demandé : $($Result.llm.model)"
Write-Host "Cartes : $($Result.stats.article_cards_count)"
Write-Host "Preuves : $($Result.stats.evidence_units_count)"
Write-Host "Verrous : $($Result.stats.verrous_count)"
Write-Host "Citations utilisées : $($Result.stats.citations_used_count)"
Write-Host "Markdown : $($Result.markdown_output_path)"
Write-Host "Payload : $ResultPath"

if ($Result.writer_used -ne "llm") {
    $LlmStatus = [string]$Result.llm.status
    throw "La sortie existe, mais le modèle de rédaction n'a pas été utilisé (llm.status=$LlmStatus)."
}
