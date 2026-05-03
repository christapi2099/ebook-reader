#!/bin/bash
set -e

if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    echo "[startup] GPU detected, CUDA acceleration available"
    export DEVICE="cuda"
else
    echo "[startup] No GPU detected, using CPU"
    export DEVICE="cpu"
fi

mkdir -p /app/uploads /data

exec "$@"
