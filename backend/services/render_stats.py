"""Per-book render statistics for ``GET /library/{id}/processing``.

Stored as a small JSON file, ``RENDERS_DIR/{book_id}/processing.json``, rather
than new Book columns: it is diagnostic data that only the worker writes and
only this endpoint reads, and it lives and dies with the book's render dir
(``delete_book`` removes it; ``gc`` leaves loose files alone).

Shape::

    {"phase_timings": {"synth": 12.3, ...}, "last_error": "...traceback...",
     "sentences_done": 10, "sentences_total": 100, "sps_ewma": 2.5,
     "updated_at": "<iso>"}

The worker calls :func:`record`; ``sps_ewma`` is maintained here from the
(sentences_done, timestamp) samples it is given.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import storage

EWMA_ALPHA = 0.3


def _path(book_id: str) -> Path:
    return Path(storage.RENDERS_DIR) / book_id / "processing.json"


def read(book_id: str) -> dict:
    try:
        return json.loads(_path(book_id).read_text())
    except (OSError, ValueError):
        return {}


def record(book_id: str, *, sentences_done: Optional[int] = None,
           sentences_total: Optional[int] = None,
           phase_timings: Optional[dict] = None,
           last_error: Optional[str] = None, now: Optional[float] = None) -> dict:
    """Merge new facts in; update the sentences/s EWMA from the done counter."""
    now = time.time() if now is None else now
    data = read(book_id)
    if sentences_done is not None:
        prev_done, prev_t = data.get("_last_done"), data.get("_last_t")
        if prev_done is not None and prev_t is not None and now > prev_t \
                and sentences_done >= prev_done:
            inst = (sentences_done - prev_done) / (now - prev_t)
            old = data.get("sps_ewma")
            data["sps_ewma"] = inst if old is None else EWMA_ALPHA * inst + (1 - EWMA_ALPHA) * old
        data["_last_done"], data["_last_t"] = sentences_done, now
        data["sentences_done"] = sentences_done
    if sentences_total is not None:
        data["sentences_total"] = sentences_total
    if phase_timings:
        data.setdefault("phase_timings", {}).update(phase_timings)
    if last_error is not None:
        data["last_error"] = last_error
    p = _path(book_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(p)
    return data
