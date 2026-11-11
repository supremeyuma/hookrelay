# HookRelay phase gate. Run from the repository root.
# Any failure stops the script; CI runs the equivalent commands individually.
$ErrorActionPreference = "Stop"

Write-Host "== format =="
uv run ruff format --check .

Write-Host "== lint =="
uv run ruff check .

Write-Host "== typecheck =="
uv run mypy app

Write-Host "== tests =="
uv run pytest

Write-Host "ALL CHECKS PASSED"
