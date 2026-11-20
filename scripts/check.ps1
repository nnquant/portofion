# Everything CI would run. Needs the dev database (deploy/compose.dev.yaml) or Docker for testcontainers.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($name, [scriptblock]$cmd) {
    Write-Host "== $name" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "$name failed" }
}

docker compose -f deploy/compose.dev.yaml up -d --wait | Out-Null
Step "ruff" { uv run ruff check . }
Step "pytest" { uv run pytest }
Step "sdk wheel" { uv build --package portofion-client --wheel --out-dir dist -q }
Set-Location web
Step "tsc" { npx tsc -b }
Step "vitest" { npx vitest run }
Step "vite build" { npx vite build --logLevel warn }
Set-Location $root
Write-Host "all checks passed" -ForegroundColor Green
