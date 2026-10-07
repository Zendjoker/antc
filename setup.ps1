# Windows: create venv, install deps, create .env
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements/base.txt
.\.venv\Scripts\python -m pip install -r requirements/local.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - free mode works as is; paid mode needs API keys." }
