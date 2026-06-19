#!/usr/bin/env bash
# Hot-reload dev mode: runs the API (auto-reload) and the Vite dev server
# (instant UI updates) together. Use this while iterating on the UI.
#
#   bash scripts/dev.sh
#   open http://localhost:5173   (Vite proxies /api -> the API on :8000)
#
# In Codespaces, open the forwarded port 5173.
set -e
cd "$(dirname "$0")/.."

[ -f data/default_world/world.json ] || python scripts/build_default_world.py
[ -d frontend/node_modules ] || (cd frontend && npm install)

cleanup() { kill 0 2>/dev/null; }
trap cleanup EXIT INT TERM

echo "==> API   http://localhost:8000  (reload)"
uvicorn glassbox.api.app:app --reload --port 8000 &

echo "==> UI    http://localhost:5173  (hot reload, proxies /api)"
(cd frontend && npm run dev) &

wait
