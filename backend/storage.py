"""Filesystem locations that deployments override through the environment."""
import os
from pathlib import Path

# RENDERS_DIR follows the DB_PATH pattern: an env var lets Docker mount a volume,
# and the default sits next to the backend code.
RENDERS_DIR = Path(os.environ.get("RENDERS_DIR") or Path(__file__).parent / "renders")
# Uploaded source files. Absolute, so importing ``main`` does not depend on the cwd.
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR") or Path(__file__).parent / "uploads")
# Piper voice models (``*.onnx`` plus their ``*.onnx.json`` configs). Lives under
# backend/voices/piper by default; ``voices/`` at the root holds custom ``.pt`` packs.
PIPER_VOICES_DIR = Path(os.environ.get("PIPER_VOICES_DIR") or Path(__file__).parent / "voices" / "piper")
# First-page cover images (PNG), one per book, made from the PDF on first request.
COVERS_DIR = Path(os.environ.get("COVERS_DIR") or Path(__file__).parent / "covers")


def book_file(file_path: str) -> Path:
    """A book's stored file. Uploads are saved as ``uploads/<id>.<ext>`` relative to
    the backend folder; resolve that here so the process cwd does not matter."""
    p = Path(file_path)
    if p.is_absolute():
        return p
    if p.parts[:1] == ("uploads",):  # honour UPLOADS_DIR when it is overridden
        return UPLOADS_DIR / Path(*p.parts[1:])
    return Path(__file__).parent / p
