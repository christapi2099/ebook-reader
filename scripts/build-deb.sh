#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DEST="$PROJECT_ROOT/deb/usr/share/kokoro-reader"

rm -rf "$DEST"
mkdir -p "$DEST"

cp "$PROJECT_ROOT/docker-compose.yml" "$DEST/"

rsync -a \
    --exclude='venv/' --exclude='.venv/' \
    --exclude='__pycache__/' --exclude='.pytest_cache/' \
    --exclude='*.db' --exclude='*.pyc' \
    --exclude='uploads/' \
    "$PROJECT_ROOT/backend/" "$DEST/backend/"

rsync -a \
    --exclude='node_modules/' --exclude='.svelte-kit/' --exclude='build/' \
    "$PROJECT_ROOT/frontend/" "$DEST/frontend/"

chmod 755 "$PROJECT_ROOT/deb/DEBIAN/postinst"

dpkg-deb --build "$PROJECT_ROOT/deb" "$PROJECT_ROOT/kokoro-reader.deb"

echo "Built kokoro-reader.deb successfully"
