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


def _active_kokoro_onnx():
    """The live KokoroOnnxEngine, or None."""
    from services import engine_manager, kokoro_onnx_engine
    cur = engine_manager.manager.current()
    return cur if isinstance(cur, kokoro_onnx_engine.KokoroOnnxEngine) else None


def current_engine_family() -> str:
    """'piper' / 'kokoro-onnx' when that engine is live; cpu, gpu and modal share 'kokoro'.
    The ONNX export is a different numerical pipeline from torch Kokoro, so it is its own
    family (a switch re-renders); the compute device it runs on is not part of it."""
    if _active_piper() is not None:
        return "piper"
    if _active_kokoro_onnx() is not None:
        return "kokoro-onnx"
    return ENGINE_FAMILY


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
    if family == "kokoro-onnx":
        eng = _active_kokoro_onnx()
        if eng is not None:
            bare = (voice or "").strip()
            if bare not in eng.available_voices():
                bare = eng.default_voice()  # same fallback the synthesis path takes
            voice = bare
            extra["model_revision"] = "kokoro-onnx-v1.0-fp32"
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
    # A finished render is never re-made on a switch: it stays readable, and it is
    # re-rendered only when the user asks (reprocess) or opens a book that is stale.
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
        if gen is not None:
            if ready_fp == fp and status in ("pending", "processing"):
                # Ready and current, but a stale job lingers: this cancels it.
                render_jobs.request_render(bid, fp)
                touched.append(bid)
            continue  # a ready render of another engine stays as it is
        if status == "error" and target_fp == fp:
            continue  # same input already failed; only an explicit reprocess retries
        render_jobs.request_render(bid, fp)
        touched.append(bid)
    return touched


def startup_sweep() -> None:
    """Run at process start: GC, reconcile missing artifacts, requeue stale fingerprints."""
    from services import render_jobs, render_store
    try:
        render_jobs.expire_orphan_leases()
        render_store.gc()
        render_store.reconcile()
        retarget_all()
    except Exception:
        logger.exception("[render] startup sweep failed")
