"""Cover art: a PNG of the first page of a PDF, made once and cached on disk."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import storage

COVER_WIDTH_PX = 320


def cover_path(book_id: str) -> Path:
    return storage.COVERS_DIR / f"{book_id}.png"


def ensure_cover(book_id: str, file_path: str, file_type: str, page: int = 0) -> Optional[Path]:
    """The cached cover, rendering it first if needed. ``None`` for non-PDF books or
    when the file is gone. Only PDFs have pages to draw."""
    if file_type != "pdf":
        return None
    target = cover_path(book_id)
    if target.is_file():
        return target
    source = storage.book_file(file_path)
    if not source.is_file():
        return None
    import fitz

    storage.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(source))
    try:
        if page >= len(doc):
            return None
        pg = doc[page]
        zoom = COVER_WIDTH_PX / pg.rect.width
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        tmp = target.with_suffix(".tmp.png")
        pix.save(str(tmp))
        tmp.replace(target)  # atomic: a reader never sees a half-written cover
    finally:
        doc.close()
    return target
