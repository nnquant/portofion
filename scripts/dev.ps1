# Dev loop: Postgres in docker, API with reload on :8000, Vite on :5173 (proxies /api to :8000).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

docker compose -f deploy/compose.dev.yaml up -d --wait
uv sync
uv run portofion migrate

$api = Start-Process -PassThru -NoNewWindow uv -ArgumentList "run", "uvicorn", "portofion_server.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload", "--reload-dir", "server/src", "--reload-dir", "engine/src"
try {
    Set-Location web
    if (-not (Test-Path node_modules)) { npm install }
    npx vite --port 5173
}
finally {
    Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
