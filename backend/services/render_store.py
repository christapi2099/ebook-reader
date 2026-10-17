"""Render artifact store: publish (rename first, then commit), GC, reconcile.

Layout: ``RENDERS_DIR/{book_id}/{gen}/`` plus ``.tmp-{uuid}`` build dirs.
Old generations are removed only by :func:`gc`, never inline.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy import delete, update
from sqlmodel import Session, select

import db.database as _db
import storage
from db.models import Book, Sentence
from services.render_state import JOB_IDLE, JOB_PENDING

CHUNK = 500
TMP_MAX_AGE_S = 24 * 3600
FAIL_POINTS = ("after_encode", "after_rename")


def renders_dir() -> Path:
    return Path(storage.RENDERS_DIR)


def _fsync_path(p: Path) -> None:
    fd = os.open(p, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(d: Path) -> None:
    for root, _dirs, files in os.walk(d, topdown=False):
        for f in files:
            _fsync_path(Path(root) / f)
        _fsync_path(Path(root))


def _fire(fail_at, point: str) -> None:
    if fail_at is None:
        return
    if callable(fail_at):
        fail_at(point)
    elif fail_at == point:
        raise RuntimeError(f"injected failure at {point}")


def publish_generation(book_id: str, tmp_dir, new_sentences: Iterable, remap: dict,
                       fingerprint: str, *, fail_at=None) -> int:
    """Atomically publish ``tmp_dir`` as the next generation; returns it."""
    tmp_dir = Path(tmp_dir)
    with Session(_db.engine) as s:
        book = s.get(Book, book_id)
        gen = book.ready_generation or 0
    new_gen = gen + 1
    final = renders_dir() / book_id / str(new_gen)

    (tmp_dir / f"remap_from_{gen}.json").write_text(
        json.dumps({str(k): v for k, v in remap.items()}))
    _fsync_tree(tmp_dir)
    _fire(fail_at, "after_encode")

    os.rename(tmp_dir, final)
    _fsync_path(final.parent)
    _fire(fail_at, "after_rename")

    rows = [dict(r) if isinstance(r, dict) else r.model_dump(exclude={"id"})
            for r in new_sentences]
    with Session(_db.engine) as s:
        s.exec(delete(Sentence).where(Sentence.book_id == book_id))
        s.commit()
    for i in range(0, len(rows), CHUNK):
        with Session(_db.engine) as s:
            s.add_all(Sentence(**{**r, "book_id": book_id}) for r in rows[i:i + CHUNK])
            s.commit()
    with Session(_db.engine) as s:
        s.exec(update(Book).where(Book.id == book_id).values(
            ready_generation=new_gen, ready_fingerprint=fingerprint,
            job_status=JOB_IDLE, phase=None, progress=1.0, error_message=None))
        s.commit()
    return new_gen


def gc(now: float | None = None) -> list[Path]:
    """Remove unreferenced generations, stale tmp dirs and orphan book dirs."""
    now = time.time() if now is None else now
    removed: list[Path] = []
    root = renders_dir()
    if not root.exists():
        return removed
    with Session(_db.engine) as s:
        ready = {b.id: b.ready_generation for b in s.exec(select(Book))}
    for bdir in root.iterdir():
        if not bdir.is_dir():
            continue
        if bdir.name not in ready:
            shutil.rmtree(bdir, ignore_errors=True)
            removed.append(bdir)
            continue
        keep = ready[bdir.name]
        for child in bdir.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith(".tmp-"):
                if now - child.stat().st_mtime > TMP_MAX_AGE_S:
                    shutil.rmtree(child, ignore_errors=True)
                    removed.append(child)
            elif child.name != str(keep):
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child)
    return removed


def _missing_artifacts(gen_dir: Path) -> bool:
    manifest = gen_dir / "manifest.json"
    if not manifest.is_file():
        return True
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return True
    tiers = data.get("tiers") or {}
    names = tiers.keys() if isinstance(tiers, dict) else [
        t.get("file") or f"audio_{t.get('requested_factor')}.opus" if isinstance(t, dict) else t
        for t in tiers]
    for n in names:
        n = str(n)
        f = n if n.endswith(".opus") else f"audio_{n}.opus"
        if not (gen_dir / f).is_file():
            return True
    return False


def reconcile() -> list[str]:
    """Mark ready books whose artifacts are missing as pending."""
    fixed: list[str] = []
    with Session(_db.engine) as s:
        for b in s.exec(select(Book).where(Book.ready_generation.is_not(None))).all():
            if _missing_artifacts(renders_dir() / b.id / str(b.ready_generation)):
                b.ready_generation = None
                b.job_status = JOB_PENDING
                b.error_message = "render artifacts missing"
                s.add(b)
                fixed.append(b.id)
        s.commit()
    return fixed


def has_room(estimate_bytes: int, *, disk_usage: Callable = shutil.disk_usage,
             path=None) -> bool:
    p = Path(path) if path else renders_dir()
    while not p.exists() and p != p.parent:
        p = p.parent
    return disk_usage(p).free > 3 * estimate_bytes
