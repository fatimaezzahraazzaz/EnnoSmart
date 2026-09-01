$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
Set-Location $projectRoot

docker compose `
  -f .\docker-compose.ovh.yml `
  -f .\docker-compose.windows.yml `
  up -d redis

docker compose `
  -f .\docker-compose.ovh.yml `
  -f .\docker-compose.windows.yml `
  ps

Write-Host ""
Write-Host "Redis EnnoSmart doit être healthy avant de lancer le worker."
