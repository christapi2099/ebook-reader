"""Old->new sentence index remapping across render generations.

Pure computation (no DB, no transaction): ``compute_remap`` is run by the render
worker before the short publish transaction. ``load_remap_chain`` and
``translate_progress_write`` read the ``remap_from_{gen-1}.json`` files that
``render_store.publish_generation`` leaves in each generation directory, so a
stale-generation progress write is translated rather than rejected.

File convention (matches render_store): publishing generation N (from ready
generation N-1) writes ``RENDERS_DIR/{id}/{N}/remap_from_{N-1}.json``, a JSON
object ``{"old_index": new_index_or_null}``.
"""
from __future__ import annotations

import bisect
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlmodel import Session, func, select

import db.database as _db
import storage
from db.models import Book, Progress, Sentence


class RemapUnavailable(Exception):
    """A remap file in the requested chain is missing or unreadable."""


def _get(s, name, default=None):
    v = s.get(name, default) if isinstance(s, dict) else getattr(s, name, default)
    return default if v is None else v


def _has_geometry(s) -> bool:
    return _get(s, "x1", 0) > _get(s, "x0", 0) or _get(s, "y1", 0) > _get(s, "y0", 0)


# ---------------------------------------------------------------- geometry
def _geometry_map(old: list, new: list, which: list[int]) -> dict[int, int | None]:
    pages: dict[int, list] = defaultdict(list)
    for j, s in enumerate(new):
        if _has_geometry(s):
            pages[_get(s, "page", 0)].append(
                ((_get(s, "y0") + _get(s, "y1")) / 2, (_get(s, "x0") + _get(s, "x1")) / 2, j))
    for lst in pages.values():
        lst.sort()
    keys = {p: [t[0] for t in lst] for p, lst in pages.items()}
    out: dict[int, int | None] = {}
    for i in which:
        s = old[i]
        lst = pages.get(_get(s, "page", 0))
        if not lst:
            out[i] = None
            continue
        cy = (_get(s, "y0") + _get(s, "y1")) / 2
        cx = (_get(s, "x0") + _get(s, "x1")) / 2
        ks = keys[_get(s, "page", 0)]
        pos = bisect.bisect_left(ks, cy)
        best, best_j = (math.inf, 1), None
        for rng in (range(pos, len(lst)), range(pos - 1, -1, -1)):
            for k in rng:
                y, x, j = lst[k]
                if abs(y - cy) > best[0]:
                    break
                d = math.hypot(y - cy, x - cx)
                score = (round(d, 6), 0 if _get(new[j], "text", "") == _get(s, "text", "") else 1)
                if score < best:
                    best, best_j = score, j
        out[i] = best_j
    return out


# -------------------------------------------------------------------- text
def _text_window(a: list[str], b: list[str]) -> list[int | None]:
    """Map positions of a into b (both lists of texts); result indexes into b."""
    res: list[int | None] = [None] * len(a)
    lo = 0
    while lo < len(a) and lo < len(b) and a[lo] == b[lo]:
        res[lo] = lo
        lo += 1
    ea, eb = len(a), len(b)
    while ea > lo and eb > lo and a[ea - 1] == b[eb - 1]:
        ea -= 1
        eb -= 1
        res[ea] = eb
    if lo < ea and lo < eb:
        sm = SequenceMatcher(None, a[lo:ea], b[lo:eb], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    res[lo + i1 + k] = lo + j1 + k
            elif tag == "replace":  # edited sentences: pair by position
                for k in range(min(i2 - i1, j2 - j1)):
                    res[lo + i1 + k] = lo + j1 + k
    return res


def _text_map(old: list, new: list, which: list[int]) -> dict[int, int | None]:
    oc: dict[int, list[int]] = defaultdict(list)
    nc: dict[int, list[int]] = defaultdict(list)
    for i in which:
        oc[_get(old[i], "chapter", 0)].append(i)
    for j, s in enumerate(new):
        nc[_get(s, "chapter", 0)].append(j)
    out: dict[int, int | None] = {}
    for ch, oidx in oc.items():
        nidx = nc.get(ch, [])
        m = _text_window([_get(old[i], "text", "") for i in oidx],
                         [_get(new[j], "text", "") for j in nidx])
        for i, r in zip(oidx, m):
            out[i] = None if r is None else nidx[r]
    return out


def compute_remap(old_sentences, new_sentences) -> list[int | None]:
    """old index (list position) -> new index (list position) or None."""
    old, new = list(old_sentences), list(new_sentences)
    geo = [i for i, s in enumerate(old) if _has_geometry(s)]
    txt = [i for i, s in enumerate(old) if not _has_geometry(s)]
    res: dict[int, int | None] = {}
    if geo:
        res.update(_geometry_map(old, new, geo))
    if txt:
        res.update(_text_map(old, new, txt))
    return [res.get(i) for i in range(len(old))]


# ------------------------------------------------------------- translation
def translate_index(remap, old_index: int, new_count: int | None = None) -> int:
    """Translate one index; never fails, never discards.

    Unmatched indexes fall back to the nearest earlier match (else the nearest
    later one); the result is clamped to ``[0, new_count - 1]`` when known, so a
    position in a deleted tail lands on the last new sentence.
    """
    old_index = max(0, old_index)
    r = None
    if remap:
        i = min(old_index, len(remap) - 1)
        r = remap[i] if old_index < len(remap) else None
        if r is None:
            for k in range(i - 1, -1, -1):
                if remap[k] is not None:
                    r = remap[k]
                    break
        if r is None:
            for k in range(i + 1, len(remap)):
                if remap[k] is not None:
                    r = remap[k]
                    break
    if r is None:  # no map, or nothing matched: keep position, clamped below
        r = old_index
    if new_count is not None:
        r = min(r, max(new_count - 1, 0))
    return max(r, 0)


def _read_remap(book_id: str, to_gen: int) -> list[int | None]:
    path = storage.RENDERS_DIR / book_id / str(to_gen) / f"remap_from_{to_gen - 1}.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise RemapUnavailable(f"{path}: {e}") from e
    n = max((int(k) for k in raw), default=-1) + 1
    out: list[int | None] = [None] * n
    for k, v in raw.items():
        out[int(k)] = v
    return out


def load_remap_chain(book_id: str, from_generation: int, to_generation: int):
    """Composed old(from_generation)->new(to_generation) map; None means identity.

    Raises RemapUnavailable if any link (e.g. GC'd) is missing.
    """
    if from_generation >= to_generation:
        return None
    chain = _read_remap(book_id, from_generation + 1)
    for g in range(from_generation + 2, to_generation + 1):
        nxt = _read_remap(book_id, g)
        chain = [None if v is None or v >= len(nxt) else nxt[v] for v in chain]
    return chain


def translate_progress_write(book_id: str, generation: int, index: int) -> int:
    """Store reading progress, translating a stale-generation write.

    A write for a generation older than Book.ready_generation is mapped through
    the remap chain (computed from files, outside any transaction) instead of
    being rejected. A missing chain degrades to clamping. Returns the index
    stored for the current generation.
    """
    with Session(_db.engine) as s:
        book = s.get(Book, book_id)
        ready = book.ready_generation if book else None
        count = s.exec(select(func.count()).select_from(Sentence)
                       .where(Sentence.book_id == book_id)).one()
    if book is None:
        raise LookupError(book_id)
    idx = max(0, index)
    if ready is not None and generation < ready:
        try:
            chain = load_remap_chain(book_id, generation, ready)
        except RemapUnavailable:
            chain = []
        idx = translate_index(chain, idx, count)
    elif count:
        idx = min(idx, count - 1)
    with Session(_db.engine) as s:
        row = s.get(Progress, book_id)
        now = datetime.now(UTC)
        if row:
            row.sentence_index, row.updated_at = idx, now
        else:
            row = Progress(book_id=book_id, sentence_index=idx, updated_at=now)
        s.add(row)
        s.commit()
    return idx
