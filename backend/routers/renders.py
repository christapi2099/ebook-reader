"""Immutable render artifacts: ``GET /api/renders/{id}/{gen}/audio_{tier}.opus``.

Every segment is validated, then the resolved path must stay inside
RENDERS_DIR (400 otherwise). Responses are immutable for a year with an ETag;
Range requests are served by Starlette's FileResponse (206 + Content-Range); a
range longer than MAX_RANGE_BYTES is cut short (see ``_capped_range``).
"""
import re
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

import storage
from services.book_processor import SERVABLE_TIERS

router = APIRouter(prefix="/api/renders", tags=["renders"])

BOOK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Filenames are audio_{t}.opus with t = str(float): 1.0, 1.5, ... Older renders
# also carry 0.75, 1.25 and 1.75, which stay servable.
ALLOWED_TIERS = frozenset(str(float(t)) for t in SERVABLE_TIERS)
IMMUTABLE = "public, max-age=31536000, immutable"
# Firefox always asks for ``bytes=N-`` and the browser then reads to the end of the
# file, up to hundreds of MB per tier element (2.7 GB for five speed switches
# measured). A 206 may legally cover less than was asked; the browser reopens at
# the new offset, so each open-ended read is cut to this size.
MAX_RANGE_BYTES = 4 * 1024 * 1024
_CHUNK = 256 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


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


def _capped_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(start, end) inclusive when a single ``bytes=a-[b]`` range is longer than
    MAX_RANGE_BYTES, else None (FileResponse serves it, or answers 416)."""
    m = _RANGE_RE.fullmatch((header or "").strip())
    if not m:
        return None
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if start >= size or end < start or end - start + 1 <= MAX_RANGE_BYTES:
        return None
    return start, start + MAX_RANGE_BYTES - 1


def _read_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    with open(path, "rb") as f:
        f.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = f.read(min(_CHUNK, left))
            if not chunk:
                return
            left -= len(chunk)
            yield chunk


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
    capped = _capped_range(request.headers.get("range"), st.st_size)
    if capped:
        start, end = capped
        return StreamingResponse(
            _read_range(path, start, end), status_code=206, media_type="audio/ogg",
            headers={**headers, "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
                     "Content-Range": f"bytes {start}-{end}/{st.st_size}"},
        )
    return FileResponse(path, media_type="audio/ogg", headers=headers)
