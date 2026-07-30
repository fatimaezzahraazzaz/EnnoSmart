param(
    [string]$Root = "C:\EnnoSmart",
    [string]$Organisme = "Scalian",
    [string]$Project = "AI_RADAR",
    [string]$Year = "2025"
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

# Charge le .env dans le processus PowerShell courant.
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
    throw "OPENAI_API_KEY est vide dans C:\EnnoSmart\.env"
}

# Configuration strictement limitée à ce test AI-RADAR.
$env:ENNOSMART_ROOT = $Root
$env:ENNOSMART_ROOT_DIR = $Root
$env:ENNOSMART_STORAGE_ROOT = Join-Path $Root "storage"
$env:ENNOSMART_STATE_OF_ART_MODE = "global"
$env:ENNOSMART_PHASE5_STATE_OF_ART_MODE = "global"
$env:ENNOSCHOLAR_PHASE5_ENABLE_LLM = "1"
$env:ENNOSCHOLAR_PHASE5_PROVIDER = "openai"
$env:ENNOSCHOLAR_PHASE5_WRITER_MODEL = "gpt-5.6-terra"
$env:ENNOSCHOLAR_REQUIRE_APPROVED_PLAN = "0"
$env:ENNOSCHOLAR_SAVE_PROMPTS = "1"
$env:ENNOSCHOLAR_MEMORY_V2_ENABLED = "0"
$env:ENNOSCHOLAR_MEMORY_V2_TOP_K = "0"
$env:ENNOSCHOLAR_VERROU_ALIASES = "529=678"

# Le mode terminal utilise l'identité de stockage du projet id=1 :
# Scalian / AI_RADAR / 2025.
$ProjectRoot = Join-Path $Root "storage\organismes\scalian\projects\ai_radar\years\$Year"
$PayloadRoot = Join-Path $ProjectRoot "ennoscholar\state_of_art_payload"

$RequiredFiles = @(
    (Join-Path $PayloadRoot "selection_payload.json"),
    (Join-Path $PayloadRoot "article_cards\article_cards_payload.json"),
    (Join-Path $PayloadRoot "phase_4_5_scientific_reasoning\scientific_reasoning_payload.json"),
    (Join-Path $PayloadRoot "phase_4_6_project_rd_argumentation\project_rd_argumentation_payload.json"),
    (Join-Path $PayloadRoot "phase_4_7_scientific_narrative\scientific_narrative_payload.json")
)

$MissingFiles = @($RequiredFiles | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($MissingFiles.Count -gt 0) {
    Write-Host ""
    Write-Host "Fichiers requis absents :" -ForegroundColor Red
    $MissingFiles | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    throw "La Phase 5 n'a pas été lancée : les artefacts précédents sont incomplets."
}

Set-Location -LiteralPath $Root

& $Python -c "from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import run_phase_5_state_of_art_writer; print('IMPORT_PHASE5_OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Échec d'import du nouvel agent EnnoScholar."
}

# Chemin volontairement inexistant : ce test sans chat utilise le plan
# scientifique de la Phase 4.7, même si un ancien contrat de chat existe.
$NoChatPlan = Join-Path $PayloadRoot "__terminal_test_without_consultant_plan__.json"

& $Python -m agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service `
    --organisme $Organisme `
    --project $Project `
    --year $Year `
    --consultant-plan-contract-path $NoChatPlan

if ($LASTEXITCODE -ne 0) {
    throw "Le processus Python de la Phase 5 a échoué."
}

$ResultPath = Join-Path $PayloadRoot "phase_5_state_of_art_writer\state_of_art_draft_payload.json"
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

$MarkdownPath = [string]$Result.markdown_output_path
Write-Host ""
Write-Host "PHASE5_OK" -ForegroundColor Green
Write-Host "Writer utilisé : $($Result.writer_used)"
Write-Host "Modèle demandé : $($Result.llm.model)"
Write-Host "Cartes : $($Result.stats.article_cards_count)"
Write-Host "Preuves : $($Result.stats.evidence_units_count)"
Write-Host "Verrous : $($Result.stats.verrous_count)"
Write-Host "Citations utilisées : $($Result.stats.citations_used_count)"
Write-Host "Markdown : $MarkdownPath"
Write-Host "Payload : $ResultPath"

if ($Result.writer_used -ne "llm") {
    $LlmStatus = [string]$Result.llm.status
    throw "La sortie existe, mais GPT-5.6 Terra n'a pas été utilisé (llm.status=$LlmStatus)."
}
