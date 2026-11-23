#!/usr/bin/env sh
# Water Autonomy Twin: create the venv on first run, then serve http://127.0.0.1:8000
set -e
cd "$(dirname "$0")/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi
PY=.venv/bin/python
[ -x "$PY" ] || PY=.venv/Scripts/python.exe
echo "Open http://127.0.0.1:8000  (API docs at /docs). First start builds the outage cache (~40 s)."
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
