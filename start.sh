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

VENV="$REPO_DIR/backend/venv"
if [ ! -f "$VENV/bin/python" ]; then
    echo "Error: backend venv not found at $VENV"
    echo "Create it with: cd backend && python3 -m venv venv && pip install -r requirements.txt"
    exit 1
fi

echo "Starting backend on http://localhost:$BACKEND_PORT ($(${VENV}/bin/python --version))"
cd "$REPO_DIR/backend"
"$VENV/bin/uvicorn" main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
BACKEND_PID=$!

echo "Starting frontend on http://localhost:$FRONTEND_PORT"
cd "$REPO_DIR/frontend"
npm run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

wait
