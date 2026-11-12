# End-to-end walkthrough. Run from the repository root after `uv sync`.
# Requires Docker Desktop and a generated .env (see README).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== migrate =="
uv run alembic upgrade head

Write-Host "== start stack =="
docker compose up -d --build

Write-Host "== wait for readiness =="
$deadline = (Get-Date).AddSeconds(180)
do {
    try {
        $ready = Invoke-RestMethod http://localhost:8000/readyz -TimeoutSec 3
        if ($ready.status -eq "ok") { break }
    } catch {
        # not ready yet
    }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)
if (-not $ready -or $ready.status -ne "ok") {
    throw "API did not become ready"
}

$sink = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "tests.fixtures.sink:app", "--port", "9099", "--log-level", "warning" `
    -PassThru -WindowStyle Hidden

try {
    Start-Sleep -Seconds 4
    Write-Host "== ingest =="
    uv run python scripts/smoke_ingest.py

    Write-Host "== delivery =="
    uv run python scripts/smoke_delivery.py

    Write-Host "== replay and scopes =="
    uv run python scripts/smoke_replay.py

    Write-Host "== trace =="
    uv run python scripts/smoke_trace.py
} finally {
    Stop-Process -Id $sink.Id -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "WALKTHROUGH PASSED"
