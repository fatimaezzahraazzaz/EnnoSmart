param(
    [string]$Destination = "C:\EnnoSmart\agents\EnnoScholar",
    [string]$Python = "python",
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceResolved = [System.IO.Path]::GetFullPath($Source)
$DestinationResolved = [System.IO.Path]::GetFullPath($Destination)

if ($SourceResolved.TrimEnd('\') -eq $DestinationResolved.TrimEnd('\')) {
    throw "La source et la destination doivent être différentes."
}

$DestinationParent = Split-Path -Parent $DestinationResolved
if (!(Test-Path $DestinationParent)) {
    New-Item -ItemType Directory -Force $DestinationParent | Out-Null
}

$Backup = $null
if (Test-Path $DestinationResolved) {
    $Backup = Join-Path $DestinationParent ("EnnoScholar_backup_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    Copy-Item $DestinationResolved $Backup -Recurse -Force
} else {
    New-Item -ItemType Directory -Force $DestinationResolved | Out-Null
}

# Copie le paquet complet, y compris les Phases 4, 4.5, 4.6, 4.7 et 5.
# Les caches Python et les fichiers compilés ne sont jamais installés.
Get-ChildItem $SourceResolved -Recurse -File |
    Where-Object {
        $_.FullName -notmatch "[\\/]+__pycache__[\\/]+" -and
        $_.Extension -ne ".pyc"
    } |
    ForEach-Object {
        $Relative = [System.IO.Path]::GetRelativePath($SourceResolved, $_.FullName)
        $Target = Join-Path $DestinationResolved $Relative
        $TargetParent = Split-Path -Parent $Target
        if (!(Test-Path $TargetParent)) {
            New-Item -ItemType Directory -Force $TargetParent | Out-Null
        }
        Copy-Item $_.FullName $Target -Force
    }

# Nettoyage d'un ancien doublon distribué dans les versions précédentes.
$ObsoleteDuplicate = Join-Path $DestinationResolved "verrou_scientific_validator.v132-api-aware.corrected.py"
if (Test-Path $ObsoleteDuplicate) {
    Remove-Item $ObsoleteDuplicate -Force
}

if (!(Test-Path (Join-Path $DestinationParent "__init__.py"))) {
    New-Item -ItemType File -Force (Join-Path $DestinationParent "__init__.py") | Out-Null
}

if (!$SkipChecks) {
    & $Python (Join-Path $DestinationResolved "check_ennoscholar_complete.py") --package $DestinationResolved
    if ($LASTEXITCODE -ne 0) {
        throw "Installation copiée, mais les contrôles ont échoué."
    }
}

Write-Host "OK - EnnoScholar complet installé dans : $DestinationResolved"
if ($Backup) {
    Write-Host "Sauvegarde de la version précédente : $Backup"
}
