$ErrorActionPreference = "Stop"
Set-Location "C:\EnnoSmart"

if (-not (Test-Path ".\.venv-mcp\Scripts\python.exe")) {
    py -3.12 -m venv .venv-mcp
}

& ".\.venv-mcp\Scripts\python.exe" -m pip install -r ".\mcp_servers\legal_fulltext_mcp\requirements.txt"
& ".\.venv-mcp\Scripts\python.exe" -m mcp_servers.legal_fulltext_mcp.server
