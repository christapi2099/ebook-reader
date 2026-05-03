#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

rm -rf "$PROJECT_ROOT/deb/opt/kokoro-reader"
mkdir -p "$PROJECT_ROOT/deb/opt/kokoro-reader"

cp "$PROJECT_ROOT/docker-compose.yml" "$PROJECT_ROOT/deb/opt/kokoro-reader/"
cp -r "$PROJECT_ROOT/backend" "$PROJECT_ROOT/deb/opt/kokoro-reader/"
cp -r "$PROJECT_ROOT/frontend" "$PROJECT_ROOT/deb/opt/kokoro-reader/"

mkdir -p "$PROJECT_ROOT/deb/usr/share/kokoro-reader"
cp "$PROJECT_ROOT/docker-compose.yml" "$PROJECT_ROOT/deb/usr/share/kokoro-reader/"
cp -r "$PROJECT_ROOT/backend" "$PROJECT_ROOT/deb/usr/share/kokoro-reader/"
cp -r "$PROJECT_ROOT/frontend" "$PROJECT_ROOT/deb/usr/share/kokoro-reader/"

cp "$PROJECT_ROOT/deb/lib/systemd/system/kokoro-reader.service" "$PROJECT_ROOT/deb/lib/systemd/system/"

chmod 755 "$PROJECT_ROOT/deb/DEBIAN/postinst"

dpkg-deb --build "$PROJECT_ROOT/deb" "$PROJECT_ROOT/kokoro-reader.deb"

echo "Built kokoro-reader.deb successfully"
