$ErrorActionPreference = "Stop"
Set-Location C:\EnnoSmart

docker compose `
  -f .\docker-compose.cir-workers.yml `
  up -d redis

docker compose `
  -f .\docker-compose.cir-workers.yml `
  ps

Write-Host ""
Write-Host "Redis EnnoSmart doit être healthy avant de lancer le worker."
