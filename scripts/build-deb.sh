#!/usr/bin/env bash
# Builds a .deb package for ebook-tts-reader.
# Requires: dpkg-deb, npm, python3 with uv, docker (for image builds)
set -euo pipefail

PACKAGE="ebook-tts-reader"
VERSION="${1:-1.0.0}"
ARCH="amd64"
MAINTAINER="christapia <chris@rolltrackapp.com>"
DESCRIPTION="Local ebook reader with Kokoro TTS sentence highlighting"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$(mktemp -d)"
DEB_ROOT="$BUILD_DIR/${PACKAGE}_${VERSION}_${ARCH}"

echo "==> Building $PACKAGE $VERSION"
echo "    Repo: $REPO_ROOT"
echo "    Work: $BUILD_DIR"

# ── 1. Build frontend static files ─────────────────────────────────────────
echo "==> Building frontend…"
cd "$REPO_ROOT/frontend"
npm ci --silent
npm run build

# ── 2. Collect files ────────────────────────────────────────────────────────
INSTALL_DIR="$DEB_ROOT/opt/ebook-tts-reader"
mkdir -p "$INSTALL_DIR/backend" \
         "$INSTALL_DIR/frontend" \
         "$DEB_ROOT/usr/share/applications" \
         "$DEB_ROOT/usr/bin" \
         "$DEB_ROOT/DEBIAN"

# Backend source (no venv — installed via postinst)
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' \
      --exclude 'uploads/' --exclude '*.db' --exclude 'tests/' \
      "$REPO_ROOT/backend/" "$INSTALL_DIR/backend/"

# Frontend build output
rsync -a "$REPO_ROOT/frontend/build/" "$INSTALL_DIR/frontend/"

# ── 3. Wrapper launchers ────────────────────────────────────────────────────
cat > "$INSTALL_DIR/start.sh" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
INSTALL="/opt/ebook-tts-reader"
DB_DIR="$HOME/.local/share/ebook-tts-reader"
mkdir -p "$DB_DIR"

cleanup() {
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Backend
cd "$INSTALL/backend"
if [ ! -d .venv ]; then
    uv venv .venv --python python3
    uv pip install --quiet -r requirements.txt
fi
DB_PATH="$DB_DIR/ebook_reader.db" .venv/bin/uvicorn main:app \
    --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# Frontend via Python http.server on the static build
python3 -m http.server 5173 --directory "$INSTALL/frontend" &
FRONTEND_PID=$!

echo "Backend PID $BACKEND_PID  Frontend PID $FRONTEND_PID"
echo "Open http://localhost:5173 in your browser."
wait
LAUNCH
chmod +x "$INSTALL_DIR/start.sh"

# /usr/bin symlink launcher
cat > "$DEB_ROOT/usr/bin/ebook-tts-reader" <<'BIN'
#!/usr/bin/env bash
exec /opt/ebook-tts-reader/start.sh "$@"
BIN
chmod +x "$DEB_ROOT/usr/bin/ebook-tts-reader"

# ── 4. Desktop entry ─────────────────────────────────────────────────────────
cat > "$DEB_ROOT/usr/share/applications/ebook-tts-reader.desktop" <<DESKTOP
[Desktop Entry]
Name=Ebook TTS Reader
Comment=$DESCRIPTION
Exec=/usr/bin/ebook-tts-reader
Icon=accessories-text-editor
Terminal=false
Type=Application
Categories=Education;Office;
DESKTOP

# ── 5. DEBIAN control files ──────────────────────────────────────────────────
INSTALLED_SIZE=$(du -sk "$DEB_ROOT/opt" | cut -f1)

cat > "$DEB_ROOT/DEBIAN/control" <<CTRL
Package: $PACKAGE
Version: $VERSION
Section: misc
Priority: optional
Architecture: $ARCH
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.11), python3-pip, ffmpeg, libsndfile1, libgl1-mesa-glx, libglib2.0-0
Recommends: nodejs (>= 18)
Maintainer: $MAINTAINER
Description: $DESCRIPTION
 Local ebook TTS reader with Kokoro neural text-to-speech, sentence-level
 highlighting, PDF/EPUB support, and a SvelteKit web UI.
CTRL

cat > "$DEB_ROOT/DEBIAN/postinst" <<'POSTINST'
#!/bin/bash
set -e
# Install uv if not present
if ! command -v uv &>/dev/null; then
    pip3 install --quiet uv
fi
# Pre-install backend Python deps system-wide (optional, may take a while)
# Users can skip by launching directly — start.sh installs in .venv on first run.
echo "ebook-tts-reader installed. Run 'ebook-tts-reader' to start."
POSTINST
chmod 0755 "$DEB_ROOT/DEBIAN/postinst"

# ── 6. Build the .deb ────────────────────────────────────────────────────────
OUTPUT="$REPO_ROOT/${PACKAGE}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$DEB_ROOT" "$OUTPUT"

echo ""
echo "==> Package: $OUTPUT"
echo "    Install: sudo dpkg -i $OUTPUT"
