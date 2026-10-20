"""Immutable render artifacts: ``GET /api/renders/{id}/{gen}/audio_{tier}.opus``.

Every segment is validated, then the resolved path must stay inside
RENDERS_DIR (400 otherwise). Responses are immutable for a year with an ETag;
Range requests are served by Starlette's FileResponse (206 + Content-Range).
"""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

import storage
from services.book_processor import SERVABLE_TIERS

router = APIRouter(prefix="/api/renders", tags=["renders"])

BOOK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Filenames are audio_{t}.opus with t = str(float): 1.0, 1.5, ... Older renders
# also carry 0.75, 1.25 and 1.75, which stay servable.
ALLOWED_TIERS = frozenset(str(float(t)) for t in SERVABLE_TIERS)
IMMUTABLE = "public, max-age=31536000, immutable"


def _bad(detail: str = "Invalid render path") -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def resolve_audio_path(book_id: str, gen: str, tier: str) -> Path:
    """Validate the three segments and return the confined file path, or raise 400."""
    if not BOOK_ID_RE.fullmatch(book_id):
        raise _bad()
    if not re.fullmatch(r"[0-9]{1,9}", gen):
        raise _bad()
    if tier not in ALLOWED_TIERS:
        raise _bad()
    root = Path(storage.RENDERS_DIR).resolve()
    path = (root / book_id / str(int(gen)) / f"audio_{tier}.opus").resolve()
    if not path.is_relative_to(root):
        raise _bad()
    return path


@router.get("/{book_id}/{gen}/audio_{tier}.opus")
def get_render_audio(book_id: str, gen: str, tier: str, request: Request):
    path = resolve_audio_path(book_id, gen, tier)
    try:
        st = path.stat()
    except OSError:
        raise HTTPException(status_code=404, detail="Render audio not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Render audio not found")
    # (book, generation, tier) is immutable once published; size+mtime guard a rebuild.
    etag = f'"{book_id}-{int(gen)}-{tier}-{st.st_size}-{st.st_mtime_ns}"'
    headers = {"Cache-Control": IMMUTABLE, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="audio/ogg", headers=headers)
