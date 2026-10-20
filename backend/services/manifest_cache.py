"""The reader's manifest: slim (schema 2) and gzipped, cached outside the render.

A published ``manifest.json`` is never touched. The first request for a
generation parses it, converts it with ``render_manifest.slim_manifest`` (a no-op
for a schema 2 render) and writes ``<cache>/<book>/<gen>-<size>-<mtime_ns>.json.gz``
next to nothing else of the render; later requests stream that file as is
(``Content-Encoding: gzip``). The source's size and mtime are in the name, so a
manifest that is ever replaced gets a new entry, and the ETag.

Measured on the 29 h Deep Learning book (358 MB schema 1 manifest, 8 tiers):
the slim form is 38.4 MB, 6.1 MB gzipped; building it once takes ~10 s and
peaks at ~1.5 GB, all returned when it is done.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

import storage
from services import render_manifest

logger = logging.getLogger(__name__)

GZIP_LEVEL = 6
_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def cache_dir() -> Path:
    """``RENDER_CACHE_DIR``, else ``render-cache`` beside ``RENDERS_DIR`` (never inside it:
    render_store.gc removes anything there that is not a book's generation)."""
    env = os.environ.get("RENDER_CACHE_DIR")
    return Path(env) if env else Path(storage.RENDERS_DIR).parent / "render-cache"


@dataclass(frozen=True)
class CachedManifest:
    path: Path  # gzipped slim JSON
    etag: str


def _lock_for(p: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(p, threading.Lock())


def encode(manifest: dict) -> bytes:
    return json.dumps(manifest, separators=(",", ":")).encode()


def slim_gzip(source: Path) -> bytes:
    """Gzipped slim JSON of the manifest at ``source`` (read only)."""
    m = json.loads(source.read_bytes())
    body = encode(render_manifest.slim_manifest(m))
    del m
    return gzip.compress(body, compresslevel=GZIP_LEVEL, mtime=0)


def get(book_id: str, gen: int, source: Path) -> CachedManifest:
    """The cached slim manifest of ``source``; built on first use. Raises OSError/ValueError."""
    st = source.stat()
    stamp = f"{int(gen)}-{st.st_size}-{st.st_mtime_ns}"
    book_cache = cache_dir() / book_id
    path = book_cache / f"{stamp}.json.gz"
    etag = f'"slim{render_manifest.SCHEMA_SLIM}-{stamp}"'
    if path.is_file():
        return CachedManifest(path, etag)
    with _lock_for(path):
        if path.is_file():  # another request built it while we waited
            return CachedManifest(path, etag)
        data = slim_gzip(source)
        book_cache.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        for old in book_cache.iterdir():  # older generations / replaced sources
            if old != path and not old.name.startswith("."):
                old.unlink(missing_ok=True)
        logger.info("[manifest] cached slim manifest %s gen %s: %d -> %d bytes gz",
                    book_id, gen, st.st_size, len(data))
    return CachedManifest(path, etag)


def remove_book(book_id: str) -> None:
    root = cache_dir().resolve()
    target = (root / book_id).resolve()
    if target != root and target.is_relative_to(root):
        shutil.rmtree(target, ignore_errors=True)


def etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    tags = [t.strip() for t in header.split(",")]
    return any(t == etag or t.removeprefix("W/") == etag for t in tags)


def accepts_gzip(header: str | None) -> bool:
    for part in (header or "").lower().split(","):
        name, _, params = part.strip().partition(";")
        if name.strip() in ("gzip", "*"):
            q = params.strip().replace(" ", "")
            if not q.startswith("q="):
                return True
            try:
                return float(q[2:]) > 0
            except ValueError:
                return True
    return False
