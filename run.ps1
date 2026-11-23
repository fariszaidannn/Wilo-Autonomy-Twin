# Water Autonomy Twin: create the venv on first run, then serve http://127.0.0.1:8000
$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "backend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    python -m venv (Join-Path $backend ".venv")
    & $py -m pip install -q -r (Join-Path $backend "requirements.txt")
}
Set-Location $backend
Write-Host "Open http://127.0.0.1:8000  (API docs at /docs). First start builds the outage cache (~40 s)."
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
