#!/usr/bin/env bash
# One command to run Glassbox: builds the default world and the frontend if
# needed, then serves the whole tool (UI + API) on a single port.
#
#   bash scripts/run.sh            # http://localhost:8000
#
# In GitHub Codespaces, open the forwarded port 8000 when it appears.
set -e
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"

if [ ! -f data/default_world/world.json ]; then
  echo "==> Building the default world + weather (~4s)…"
  python scripts/build_default_world.py
fi

if [ ! -d frontend/dist ]; then
  echo "==> Building the frontend (first run only)…"
  (cd frontend && npm install && npm run build)
fi

echo "==> Serving Glassbox on http://localhost:${PORT}"
exec uvicorn glassbox.api.app:app --host 0.0.0.0 --port "${PORT}"
