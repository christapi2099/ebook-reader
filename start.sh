#!/bin/bash
set -e

if [ "$(id -u)" -eq 0 ]; then
    echo "Error: don't run this with sudo. Just run: ./start.sh"
    exit 1
fi

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT=8000
FRONTEND_PORT=5173

cleanup() {
    echo ""
    echo "Shutting down..."
    kill -- -$BACKEND_PID -$FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
    echo "Done."
}
trap cleanup EXIT INT TERM
set -m

for PORT in $BACKEND_PORT $FRONTEND_PORT; do
  PID=$(lsof -ti :"$PORT" 2>/dev/null || true)
  if [ -n "$PID" ]; then
    echo "Killing process(es) on port $PORT (PID: $PID)"
    kill $PID 2>/dev/null
    sleep 0.5
  fi
done

# Backend environment, in order of preference:
#   1. uv            -> `uv run` syncs backend/.venv from backend/uv.lock, then runs
#   2. backend/.venv -> uv-managed env without uv on PATH
#   3. backend/venv  -> legacy hand-made venv, kept working for un-migrated checkouts
UV_BIN="$(command -v uv || true)"
if [ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
fi

cd "$REPO_DIR/backend"
if [ -n "$UV_BIN" ]; then
    echo "Starting backend on http://localhost:$BACKEND_PORT (uv run; syncing .venv from uv.lock)"
    "$UV_BIN" run uvicorn main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
elif [ -x "$REPO_DIR/backend/.venv/bin/uvicorn" ]; then
    echo "Starting backend on http://localhost:$BACKEND_PORT (.venv; uv not found on PATH)"
    "$REPO_DIR/backend/.venv/bin/uvicorn" main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
elif [ -x "$REPO_DIR/backend/venv/bin/uvicorn" ]; then
    LEGACY_VENV="$REPO_DIR/backend/venv"
    echo "Starting backend on http://localhost:$BACKEND_PORT (legacy env: $(${LEGACY_VENV}/bin/python --version))"
    "$LEGACY_VENV/bin/uvicorn" main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
else
    echo "Error: no backend environment found (looked for uv, backend/.venv, backend/venv)"
    echo "Install uv (https://docs.astral.sh/uv/) then run: cd backend && uv sync"
    exit 1
fi
BACKEND_PID=$!

echo "Starting frontend on http://localhost:$FRONTEND_PORT"
cd "$REPO_DIR/frontend"
npm run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

wait
