"""Process-first render triggers shared by the routers (upload, voice change,
reprocess, engine switch, startup).

A trigger computes the fingerprint a render *would* have today and hands it to
``render_jobs.request_render``. The book stays readable on its old generation.
Compute device (cpu/gpu/modal) is not a fingerprint input, so only a change of
engine *family* retargets books.
"""
from __future__ import annotations

import functools
import logging
from typing import Optional

from sqlmodel import Session, select

import db.database as _db
from db.models import Book
from services import render_jobs
from services.book_processor import current_inputs, fingerprint

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "af_heart"
ENGINE_FAMILY = "kokoro"


def _active_piper():
    """The live PiperEngine, or None (cpu/gpu/modal all run Kokoro)."""
    from services import engine_manager, piper_engine
    cur = engine_manager.manager.current()
    return cur if piper_engine.is_piper(cur) else None


def current_engine_family() -> str:
    """'piper' when the live engine is Piper; cpu, gpu and modal share 'kokoro'."""
    return "piper" if _active_piper() is not None else ENGINE_FAMILY


@functools.lru_cache(maxsize=1)
def _ffmpeg_version() -> str:
    try:
        from services.book_processor import _ffmpeg_version as probe
        return probe()
    except Exception:  # ffmpeg missing: a stable placeholder keeps fingerprints comparable
        return "unknown"


def target_fingerprint_for(voice: Optional[str]) -> str:
    """Fingerprint of a render of ``voice`` with today's pipeline and engine family."""
    family = current_engine_family()
    extra = {}
    eng = _active_piper() if family == "piper" else None
    if eng is not None:
        bare = (voice or "").split(":", 1)[-1] if voice else ""
        if not bare or bare not in eng.available_voices():
            bare = eng.default_voice()  # same fallback the synthesis path takes
        voice = f"piper:{bare}"
        extra["voice_path"] = str(eng._dir / f"{bare}.onnx")
    inputs = current_inputs(voice or DEFAULT_VOICE, family,
                            ffmpeg_version=_ffmpeg_version(), **extra)
    return fingerprint(inputs)


def request_for_book(book_id: str, voice: Optional[str], *, debounce: bool = True) -> str:
    return render_jobs.request_render(
        book_id, target_fingerprint_for(voice), debounce=debounce)


def retarget_all() -> list[str]:
    """Request a render for every book whose fingerprint no longer matches what
    it has (or is heading for). Used by the engine switch and at startup.
    Returns the ids touched."""
    touched: list[str] = []
    with Session(_db.engine) as s:
        books = [(b.id, b.voice, b.ready_generation, b.ready_fingerprint,
                  b.target_fingerprint, b.job_status) for b in s.exec(select(Book))]
    cache: dict[Optional[str], str] = {}
    for bid, voice, gen, ready_fp, target_fp, status in books:
        fp = cache.get(voice)
        if fp is None:
            fp = cache[voice] = target_fingerprint_for(voice)
        up_to_date = (gen is not None and ready_fp == fp
                      and target_fp in (None, fp) and status not in ("pending", "processing"))
        in_flight = target_fp == fp and status in ("pending", "processing")
        if up_to_date or in_flight:
            continue
        if gen is not None and ready_fp == fp:
            # Ready and current, but a stale job lingers: this cancels it.
            render_jobs.request_render(bid, fp)
            touched.append(bid)
            continue
        if status == "error" and target_fp == fp:
            continue  # same input already failed; only an explicit reprocess retries
        render_jobs.request_render(bid, fp)
        touched.append(bid)
    return touched


def startup_sweep() -> None:
    """Run at process start: GC, reconcile missing artifacts, requeue stale fingerprints."""
    from services import render_store
    try:
        render_store.gc()
        render_store.reconcile()
        retarget_all()
    except Exception:
        logger.exception("[render] startup sweep failed")
