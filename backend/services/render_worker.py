"""Process-first render worker: pipeline (``render_book``) and loop (``worker_loop``).

One book is rendered from sentences to a complete immutable generation:

    extract/load -> [check_job, synthesize ONE sentence, write chunk, align,
    map boxes, checkpoint] per sentence -> build_base -> build_tiers -> drift
    audit -> manifest -> remap -> ``render_store.publish_generation``.

Everything stateful already exists and is reused: ``render_jobs`` (claim, lease,
supersede/cancel checks), ``render_store`` (rename-then-commit publish, GC,
disk guard), ``render_artifacts`` (base/tiers/audit/manifest), ``aligner``,
``word_boxes``, ``remap``. This module only sequences them.

Resume: the working dir is ``RENDERS_DIR/{id}/.tmp-{uuid}``. A sentence is done
when both ``chunks/{i}.flac`` and ``chunks/{i}.json`` (its aligned words) exist;
``checkpoint.json`` holds the target fingerprint the dir was built for. The
worker never reads ``AudioCache`` to resume.

Seams (all injectable for hermetic tests):

* ``synthesizer(text, voice) -> SynthResult`` (blocking, one sentence).
* ``fallback_synthesizer`` used after the primary fails 1 + ``retries`` times
  (Modal outage); the manifest then records ``mixed_backends=true``.
* ``aligner`` (see ``services.aligner.Aligner``), ``extractor``, ``box_provider``.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import os
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf
from sqlalchemy import update
from sqlmodel import Session, select

import db.database as _db
import storage
from services import render_pause
from db.models import Book, Sentence
from services import (
    book_processor, render_artifacts as ra, render_jobs as rj, render_store as rs,
)
from services.aligner import align_sentence, distribute_by_syllables
from services.env_config import positive_int
from services.remap import compute_remap
from services.render_state import JOB_ERROR, JOB_PENDING
from services.word_boxes import map_words_to_boxes

logger = logging.getLogger(__name__)

SR = ra.SR
LEASE_RENEW_S = 30.0
# A render that reports no progress for this long is hung (a stuck ffmpeg or
# CUDA call), not slow: the heartbeat keeps renewing its lease, so only progress
# tells the two apart. Generous, because one encode step can run for minutes.
STALL_TIMEOUT_S = float(os.environ.get("RENDER_STALL_TIMEOUT_S", "900"))
MODAL_RETRIES = 3
# Rough bytes of base FLAC + all Opus tiers per hour of speech (spike, ~141 MB/h)
# and speech rate (chars per second) used to size the disk guard.
BYTES_PER_AUDIO_HOUR = 141_000_000
CHARS_PER_SECOND = 15.0

READY = "ready"
REQUEUED = "requeued"
CANCELLED = "cancelled"
GONE = "gone"
ERROR = "error"
BLOCKED = "blocked_on_disk"
LOST = "lost_lease"


@dataclass
class SynthResult:
    """One synthesized sentence. ``native_words`` is Piper [(start_s, end_s)],
    ``kokoro_tokens`` is [(text, start_s, end_s)]; both must come from the same
    synthesis call as ``audio``."""
    audio: np.ndarray
    effective_speed: float = 1.0
    native_words: Optional[list] = None
    kokoro_tokens: Optional[list] = None


class _Stop(Exception):
    """Internal: unwind the pipeline with an outcome (not an error)."""

    def __init__(self, outcome: str):
        super().__init__(outcome)
        self.outcome = outcome


# ----------------------------------------------------------------- defaults
class _EngineSynth:
    """Blocking one-sentence seam over ``tts_engine.synthesize_render_result`` (one task
    on the shared ``_synthesis_pool``). Carries no tokens, so words are CTC-aligned.

    ``parallel_safe``/``for_worker`` let the render fan out over independent engine
    clones when the engine allows it (Piper); live playback keeps the shared pool."""

    def __init__(self, engine=None, speed: float = 1.0):
        self.engine, self.speed = engine, speed

    def _engine(self):
        from services import tts_engine
        return self.engine or tts_engine._render_engine

    @property
    def parallel_safe(self) -> bool:
        eng = self._engine()
        return bool(eng is not None and getattr(eng, "parallel_safe", False))

    @property
    def default_workers(self) -> int:
        """Workers when ``RENDER_SYNTH_WORKERS`` is unset (the engine's own default, else 1)."""
        fn = getattr(self._engine(), "default_render_workers", None)
        return int(fn()) if callable(fn) else 1

    def open_workers(self, requested: int) -> int:
        """Let the engine start its worker resources (GPU processes); -> usable workers."""
        fn = getattr(self._engine(), "open_render_workers", None)
        return int(fn(requested)) if callable(fn) else requested

    def window_threads(self, workers: int) -> int:
        fn = getattr(self._engine(), "render_window_threads", None)
        return int(fn(workers)) if callable(fn) else workers

    def close_workers(self) -> None:
        fn = getattr(self._engine(), "close_render_workers", None)
        if callable(fn):
            fn()

    def __call__(self, text: str, voice: str) -> SynthResult:
        import asyncio

        from services import tts_engine

        audio, eff, native, tokens = asyncio.run(
            tts_engine.synthesize_render_result(text, voice, self.speed, engine=self.engine))
        if audio is None:
            raise ra.RenderError("synthesis produced no audio", phase="synthesize")
        return SynthResult(np.asarray(audio, dtype=np.float32), eff, native, tokens)

    def for_worker(self, threads: Optional[int] = None) -> Callable[[str, str], SynthResult]:
        """A synthesizer for ONE render thread: its own engine clone, off the shared pool."""
        import asyncio

        from services import render_throttle

        clone = self._engine().clone_for_render_worker(threads)

        def _synth(text: str, voice: str) -> SynthResult:
            asyncio.run(render_throttle.await_playback_idle())  # still yield to playback
            audio, native, *rest = clone.synthesize_for_render(text, voice)
            if audio is None:
                raise ra.RenderError("synthesis produced no audio", phase="synthesize")
            # The GPU worker pool also returns Kokoro tokens (3rd item), like the serial path.
            return SynthResult(np.asarray(audio, dtype=np.float32), 1.0, native,
                               rest[0] if rest else None)

        return _synth


def engine_synthesizer(engine=None, speed: float = 1.0) -> _EngineSynth:
    """Adapt ``tts_engine.synthesize_sentence_for_render`` (async, one sentence per
    pool task) to the blocking seam."""
    return _EngineSynth(engine, speed)


# --------------------------------------------------------- parallel synthesis
def synth_workers_setting() -> int:
    """``RENDER_SYNTH_WORKERS`` (default 1 = today's serial loop)."""
    return positive_int("RENDER_SYNTH_WORKERS", 1)


def synth_workers_explicit() -> Optional[int]:
    """The value of ``RENDER_SYNTH_WORKERS`` when it is set to something usable, else None."""
    from services.env_config import positive_number
    v = positive_number(os.environ.get("RENDER_SYNTH_WORKERS"), "RENDER_SYNTH_WORKERS")
    return None if v is None else int(v)


def synth_threads_total() -> int:
    """``RENDER_THREADS_TOTAL`` (default: cpu count), split across synthesis workers."""
    return positive_int("RENDER_THREADS_TOTAL", os.cpu_count() or 1)


# ORT intra-op threads beyond this did not help a Piper/CTC session (measured: 8 threads
# 2.16 s vs 112 threads 3.28 s for 23 sentences) and only burn cores spinning.
MAX_THREADS_PER_WORKER = 8
_warned_serial = False


def resolve_synth_workers(synthesizer) -> int:
    """Workers actually used: an explicit ``RENDER_SYNTH_WORKERS`` always wins, otherwise the
    engine's own default (4 on the torch GPU engine, 1 elsewhere); 1 unless the synthesizer is
    parallel-safe."""
    global _warned_serial
    n = synth_workers_explicit()
    if n is None:
        n = int(getattr(synthesizer, "default_workers", 1) or 1)
    if n <= 1:
        return 1
    if not (getattr(synthesizer, "parallel_safe", False)
            and callable(getattr(synthesizer, "for_worker", None))):
        if not _warned_serial:
            _warned_serial = True
            logger.warning("[render] RENDER_SYNTH_WORKERS=%d ignored: this engine is not "
                           "parallel-safe; rendering serially", n)
        return 1
    return n


def _shared_aligner(workers: int):
    """ONE CTC aligner shared by all synthesis workers. The model is ~1.3 GB, so a session
    per worker would cost gigabytes; ORT ``run`` is thread-safe and the native alignment is
    reentrant (locals only), and a shared session measured as fast as per-worker ones.
    Threads scale with the workers (2 each, at least the default 8, within the budget)."""
    from services import aligner as al

    default = al.default_aligner()
    want = min(max(synth_threads_total(), 1), max(default.threads, 2 * workers))
    return default if want == default.threads else al.CtcOnnxAligner(threads=want)


class _SynthWindow:
    """Bounded look-ahead of one-sentence syntheses on a private thread pool.

    The consumer asks for results strictly in order; ``prepare(k)`` keeps positions
    ``k .. k+window-1`` scheduled (skipping positions whose chunk already exists).
    Each pool thread owns one synthesizer (``factory(threads)``, built lazily on that
    thread). Nothing here touches the job, DB, stats or files."""

    def __init__(self, todo, voice, synth, factory, workers, threads, is_done, touch,
                 aligner=None):
        self._todo, self._voice, self._synth = todo, voice, synth
        self._aligner = aligner
        self._factory, self._threads = factory, threads
        self._is_done, self._touch = is_done, touch
        self._window = 2 * workers
        self._pool = cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="render-synth")
        self._tls = threading.local()
        self._futs: dict[int, cf.Future] = {}
        self._next = 0
        self._closed = False

    def _run(self, text: str):
        """-> ``(SynthResult, word timings or None)``. Alignment (CTC, ~2x the cost of a
        Piper synthesis) runs here, on the worker, with a per-thread aligner, because left
        in the ordered consumer it would be the serial bottleneck. ``None`` timings means
        the result failed a check the consumer must raise in the serial path's order."""
        prim = getattr(self._tls, "primary", None)
        if prim is None:  # lazy, once per thread, on that thread
            prim = self._tls.primary = self._factory(self._threads)
        try:
            res = self._synth(text, self._voice, primary=prim)
            audio = np.asarray(res.audio, dtype=np.float32)
            if res.effective_speed != 1.0 or audio.size == 0:
                return res, None
            return res, align_sentence(audio, SR, text, native_words=res.native_words,
                                       kokoro_tokens=res.kokoro_tokens, aligner=self._aligner)
        finally:
            self._touch()

    def prepare(self, k: int) -> None:
        stop = min(len(self._todo), k + self._window)
        self._next = max(self._next, k)
        while self._next < stop and not self._closed:
            j = self._next
            self._next += 1
            if not self._is_done(self._todo[j]["index"]):
                self._futs[j] = self._pool.submit(self._run, self._todo[j]["text"])

    def future(self, k: int) -> Optional[cf.Future]:
        return self._futs.pop(k, None)

    def close(self) -> None:
        """Cancel queued work and wait for the (at most ``workers``) running calls."""
        self._closed = True
        for f in self._futs.values():
            f.cancel()
        self._futs.clear()
        self._pool.shutdown(wait=True)


STATS_EVERY = 10
# A sentence the engine cannot speak (no phonemes) is rendered as this much silence.
SILENT_SENTENCE_MS = 300


def _stats(book_id: str, **kw) -> None:
    """Short, best-effort write to render_stats (never fails a render)."""
    try:
        from services import render_stats
        render_stats.record(book_id, **kw)
    except Exception:  # noqa: BLE001
        logger.debug("[render] stats write failed", exc_info=True)


def _row(s: Sentence) -> dict:
    return s.model_dump(exclude={"id", "book_id"})


def db_sentences(book_id: str) -> list[dict]:
    with Session(_db.engine) as s:
        return [_row(r) for r in s.exec(
            select(Sentence).where(Sentence.book_id == book_id).order_by(Sentence.index))]


def default_extractor(book: Book) -> list[dict]:
    """Re-extract PDF/EPUB from the stored file (so a changed text pipeline takes
    effect); anything else (pasted text) keeps its stored sentences."""
    path = storage.book_file(book.file_path)
    if book.file_type not in ("pdf", "epub") or not path.is_file():
        return db_sentences(book.id)
    from services.epub_engine import EPUBEngine
    from services.pdf_engine import PDFEngine
    from services.text_cleaner import normalize_text
    from services.text_filter import TextFilter

    tf = TextFilter()
    pdf = book.file_type == "pdf"
    raw = (PDFEngine() if pdf else EPUBEngine()).extract_sentences(str(path))
    return [dict(
        index=r.index, text=normalize_text(r.text),
        page=r.page if pdf else 0, x0=r.x0 if pdf else 0.0, y0=r.y0 if pdf else 0.0,
        x1=r.x1 if pdf else 0.0, y1=r.y1 if pdf else 0.0,
        filtered=tf.should_filter(r.text),
        words=json.dumps(r.words) if pdf else None,
        chapter=r.chapter, chapter_title=r.chapter_title) for r in raw]


def pdf_box_provider(book: Book) -> Optional[Callable[[dict], list]]:
    """sentence row -> PyMuPDF word tuples of that sentence only. The stored
    ``words`` hold boxes without text, so the text is recovered from the page by
    exact coordinates. EPUB (no geometry) returns None: every box is None."""
    if book.file_type != "pdf" or not storage.book_file(book.file_path).is_file():
        return None
    import fitz

    doc = fitz.open(str(storage.book_file(book.file_path)))
    pages: dict[int, dict] = {}

    def key(x0, y0, x1, y1):
        return (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3))

    def provider(sent: dict) -> list:
        page = sent.get("page") or 0
        if page not in pages:
            pages[page] = {key(*w[:4]): w for w in doc[page].get_text("words")}
        out = []
        for w in json.loads(sent.get("words") or "[]"):
            hit = pages[page].get(key(w["x0"], w["y0"], w["x1"], w["y1"]))
            if hit is not None:
                out.append(hit)
        return out

    provider.close = doc.close  # type: ignore[attr-defined]
    return provider


# ---------------------------------------------------------------- DB helpers
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _update_book(book_id: str, **values) -> bool:
    with _db.engine.begin() as conn:
        res = conn.execute(update(Book.__table__).where(Book.__table__.c.id == book_id)
                           .values(**values))
    return res.rowcount == 1


def _load_book(book_id: str) -> Optional[Book]:
    with Session(_db.engine) as s:
        b = s.get(Book, book_id)
        if b is not None:
            s.expunge(b)
        return b


def _set_error(book_id: str, worker_id: str, phase: str, message: str) -> None:
    with _db.engine.begin() as conn:
        t = Book.__table__.c
        conn.execute(update(Book.__table__).where(t.id == book_id, t.lease_owner == worker_id)
                     .values(job_status=JOB_ERROR, phase=phase, error_message=message,
                             lease_owner=None, lease_expires=None, pending_since=None))


def _block_on_disk(book_id: str, worker_id: str, now: datetime) -> None:
    rj.requeue(book_id, worker_id)
    # pending_since=now makes claim_next's debounce absorb the retry; re-check later.
    _update_book(book_id, phase=BLOCKED, pending_since=now,
                 error_message="not enough free disk space for a render")


def _remove_book_dir(book_id: str) -> None:
    shutil.rmtree(storage.RENDERS_DIR / book_id, ignore_errors=True)


# --------------------------------------------------------------- checkpoint
def _write_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def _speakable(row: dict) -> bool:
    """A row the engine should synthesize: not filtered, and with something to say.
    Leftovers such as a lone "." from a table of contents have no audio in Piper,
    and used to fail the whole book."""
    return not row.get("filtered") and any(c.isalnum() for c in row["text"])


def _find_or_make_tmp(book_id: str, target: str) -> tuple[Path, dict]:
    """Reuse the tmp dir built for ``target`` (resume); delete tmp dirs built for
    any other target; else start a fresh one."""
    root = storage.RENDERS_DIR / book_id
    root.mkdir(parents=True, exist_ok=True)
    found: Optional[tuple[Path, dict]] = None
    for d in sorted(root.glob(".tmp-*")):
        try:
            cp = json.loads((d / "checkpoint.json").read_text())
        except (OSError, ValueError):
            cp = None
        if cp and cp.get("target_fingerprint") == target and found is None:
            found = (d, cp)
        else:
            shutil.rmtree(d, ignore_errors=True)
    if found:
        return found
    d = root / f".tmp-{uuid.uuid4().hex}"
    (d / "chunks").mkdir(parents=True)
    cp = {"target_fingerprint": target, "mixed_backends": False}
    _write_json(d / "checkpoint.json", cp)
    return d, cp


def _chunk_done(tmp: Path, index: int) -> bool:
    """Cheap existence check of a finished chunk (sidecar is written last)."""
    return (tmp / "chunks" / f"{index}.flac").is_file() and (tmp / "chunks" / f"{index}.json").is_file()


def _load_done(tmp: Path, index: int) -> Optional[tuple[np.ndarray, list]]:
    flac, meta = tmp / "chunks" / f"{index}.flac", tmp / "chunks" / f"{index}.json"
    if not (flac.is_file() and meta.is_file()):
        return None
    try:
        audio, _ = sf.read(flac, dtype="float32")
        words = json.loads(meta.read_text())["words"]
    except (OSError, ValueError, RuntimeError, KeyError):
        return None
    return audio, words


# ----------------------------------------------------------------- synthesis
class _Synth:
    """Primary synthesizer with Modal-style retry, then a sticky local fallback."""

    def __init__(self, primary, fallback, retries, sleep, mixed: bool):
        self.primary, self.fallback = primary, fallback
        self.retries, self.sleep = retries, sleep
        self.mixed = mixed
        self.on_fallback: Callable[[], None] = lambda: None
        # Parallel synthesis calls this from several threads: the sticky flip and the
        # checkpoint write happen once, and the (single, possibly non-reentrant) fallback
        # engine is never entered concurrently.
        self._lock = threading.Lock()

    def __call__(self, text: str, voice: str, primary=None) -> SynthResult:
        primary = primary or self.primary
        if not self.mixed:
            attempts = 1 + (self.retries if self.fallback else 0)
            last: Optional[Exception] = None
            for n in range(attempts):
                try:
                    return primary(text, voice)
                except Exception as e:  # noqa: BLE001 - any backend failure is retryable
                    last = e
                    if self.fallback is None:
                        raise
                    if n < attempts - 1:
                        self.sleep(min(2.0 ** n, 30.0))
            with self._lock:
                if not self.mixed:
                    logger.warning("[render] primary backend failed %d times (%s); using "
                                   "fallback", attempts, last)
                    self.mixed = True
                    self.on_fallback()
        with self._lock:
            return self.fallback(text, voice)


def _estimate_bytes(rows: list[dict]) -> int:
    chars = sum(len(r["text"]) for r in rows if _speakable(r))
    return int(chars / CHARS_PER_SECOND / 3600 * BYTES_PER_AUDIO_HOUR)


def _clamp_words(words: list[dict], dur_ms: float) -> list[dict]:
    out = []
    for w in words:
        e = min(float(w["end_ms"]), dur_ms)
        out.append({**w, "start_ms": min(float(w["start_ms"]), e), "end_ms": e})
    return out


# ------------------------------------------------------------------ pipeline
def render_book(book_id: str, worker_id: str, *, synthesizer, aligner=None,
                fallback_synthesizer=None, extractor=None, box_provider=None,
                tiers=None, now: Optional[Callable[[], datetime]] = None,
                clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep,
                disk_usage: Callable = shutil.disk_usage,
                renew_every_s: float = LEASE_RENEW_S, lease_s: int = 120,
                retries: int = MODAL_RETRIES, fail_at=None,
                raise_errors: bool = False, stall_s: float = STALL_TIMEOUT_S) -> str:
    """Render a CLAIMED book. Returns READY | REQUEUED | CANCELLED | GONE | ERROR |
    BLOCKED | LOST. Failures become ``job_status='error'`` (traceback in
    ``error_message``, ``phase`` = the failing phase) unless ``raise_errors``."""
    now = now or _utcnow
    ctx: dict = {}
    heartbeat = _Heartbeat(book_id, worker_id, now, lease_s, renew_every_s, stall_s)
    ctx["heartbeat"] = heartbeat
    heartbeat.start()
    try:
        return _render(book_id, worker_id, synthesizer, aligner, fallback_synthesizer,
                       extractor, box_provider, tiers, now, clock, sleep, disk_usage,
                       renew_every_s, lease_s, retries, fail_at, ctx)
    except _Stop as st:
        return st.outcome
    except Exception as e:  # noqa: BLE001
        if raise_errors:
            raise
        logger.exception("[render] %s failed", book_id)
        if _load_book(book_id) is None:  # deleted mid-render
            _remove_book_dir(book_id)
            return GONE
        if rj.check_job(book_id, ctx.get("target")) == rj.SUPERSEDED:
            rj.requeue(book_id, worker_id)  # failed on a stale target: don't park it in error
            return REQUEUED
        phase = getattr(e, "phase", "render")
        _stats(book_id, last_error=traceback.format_exc())
        _set_error(book_id, worker_id, phase, traceback.format_exc())
        return ERROR
    finally:
        heartbeat.stop()


class _Heartbeat:
    """Renews the lease on a timer for the whole render, including the encode and
    audit phases that do not reach the per-sentence renewal. A lease that lapses
    shows the book as queued and lets another worker claim it.

    It is also the stall watchdog. Renewing a lease only shows the process is
    alive, so a synthesis call that hangs would hold the lease for ever. The render
    calls :meth:`touch` whenever it makes progress; if that stops for ``stall_s``
    the book is marked ``error`` and the lease is no longer renewed. The hung call
    itself cannot be killed, so the worker thread stays blocked until it returns
    (or the process restarts), but the failure is visible instead of a bar frozen
    on "processing"."""

    def __init__(self, book_id, worker_id, now, lease_s, every_s,
                 stall_s: float = STALL_TIMEOUT_S, clock: Callable[[], float] = time.monotonic):
        self._args = (book_id, worker_id, now, lease_s)
        self._every = every_s
        self._stall_s = stall_s
        self._clock = clock
        self._last_progress = clock()
        self._phase = "extract"
        self.stalled = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"lease-{book_id[:8]}",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def touch(self, phase: Optional[str] = None) -> None:
        """The render made progress."""
        self._last_progress = self._clock()
        if phase:
            self._phase = phase

    def _run(self) -> None:
        book_id, worker_id, now, lease_s = self._args
        while not self._stop.wait(self._every):
            idle = self._clock() - self._last_progress
            if idle >= self._stall_s:
                self.stalled = True
                logger.error("[render] %s made no progress for %.0fs in phase %s; "
                             "marking it stalled", book_id, idle, self._phase)
                try:
                    _set_error(book_id, worker_id, self._phase,
                               f"Render stalled: no progress for {int(idle)}s in the "
                               f"{self._phase} phase. The synthesis or encode call is "
                               "probably hung; restart the backend, then retry.")
                except Exception:  # noqa: BLE001
                    logger.exception("[render] could not record the stall for %s", book_id)
                return
            try:
                if not rj.renew_lease(book_id, worker_id, now(), lease_s):
                    return  # lease lost: the main loop sees it at its next checkpoint
            except Exception:  # noqa: BLE001
                logger.debug("[render] heartbeat renew failed", exc_info=True)


def _render(book_id, worker_id, synthesizer, aligner, fallback, extractor, box_provider,
            tiers, now, clock, sleep, disk_usage, renew_every_s, lease_s, retries, fail_at, ctx):
    book = _load_book(book_id)
    if book is None:
        _remove_book_dir(book_id)
        return GONE
    target = book.target_fingerprint
    if target is None:
        from services import render_triggers
        target = render_triggers.target_fingerprint_for(book.voice)
        _update_book(book_id, target_fingerprint=target)
    voice = book.voice or "af_heart"
    ctx["target"] = target

    heartbeat = ctx["heartbeat"]

    def checkpoint_job(drop_tmp=False) -> None:
        """Between-step supersede/cancel/delete/pause check."""
        if heartbeat.stalled:  # a hung call came back after the watchdog gave up
            raise _Stop(ERROR)
        verdict = rj.check_job(book_id, target)
        if verdict == rj.CONTINUE:
            if render_pause.is_paused():
                # The user asked to read: keep the checkpoint and give the job back.
                rj.pause(book_id, worker_id)
                raise _Stop(REQUEUED)
            return
        if verdict == rj.GONE:
            _remove_book_dir(book_id)
            raise _Stop(GONE)
        if verdict == rj.CANCELLED:
            for d in (storage.RENDERS_DIR / book_id).glob(".tmp-*"):
                shutil.rmtree(d, ignore_errors=True)
            rj.release(book_id, worker_id)
            raise _Stop(CANCELLED)
        rj.requeue(book_id, worker_id)  # SUPERSEDED: the new target gets its own run
        raise _Stop(REQUEUED)

    def progress(frac: float, phase: str) -> None:
        if heartbeat.stalled:
            raise _Stop(ERROR)
        heartbeat.touch(phase)
        if not rj.mark_progress(book_id, frac, phase):
            _remove_book_dir(book_id)
            raise _Stop(GONE)

    phase_t = {"name": "extract", "t": clock()}

    def phase_stat(name: str) -> None:
        """Close the running phase's wall time and open ``name``."""
        t = clock()
        _stats(book_id, phase_timings={phase_t["name"]: round(t - phase_t["t"], 3)})
        phase_t["name"], phase_t["t"] = name, t

    progress(0.0, "extract")
    rows = (extractor or default_extractor)(book)
    todo = [r for r in rows if _speakable(r)]
    phase_stat("synthesize")
    _stats(book_id, sentences_done=0, sentences_total=len(todo), work_done=0, now=time.time())
    fresh = 0  # sentences synthesized in this run; resumed ones are not work
    if not todo:
        raise ra.RenderError("book has no readable sentences", phase="extract")

    if not rs.has_room(_estimate_bytes(rows), disk_usage=disk_usage,
                       path=storage.RENDERS_DIR):
        _block_on_disk(book_id, worker_id, now())
        return BLOCKED

    tmp, cp = _find_or_make_tmp(book_id, target)
    synth = _Synth(synthesizer, fallback, retries, sleep, bool(cp.get("mixed_backends")))

    def _mark_mixed():
        cp["mixed_backends"] = True
        _write_json(tmp / "checkpoint.json", cp)
    synth.on_fallback = _mark_mixed

    provider = box_provider if box_provider is not None else pdf_box_provider(book)
    last_renew = clock()
    chunks: list[dict] = []
    window: Optional[_SynthWindow] = None
    workers = resolve_synth_workers(synthesizer)
    opener = getattr(synthesizer, "open_workers", None)
    if workers > 1 and callable(opener):
        workers = opener(workers)  # may start GPU processes; VRAM-capped, can come back as 1
    if workers > 1:
        threads = max(1, min(MAX_THREADS_PER_WORKER, synth_threads_total() // workers))
        logger.info("[render] %s: %d synthesis workers x %d threads", book_id, workers, threads)
        wt = getattr(synthesizer, "window_threads", None)
        window = _SynthWindow(todo, voice, synth, synthesizer.for_worker,
                              wt(workers) if callable(wt) else workers, threads,
                              lambda i: _chunk_done(tmp, i), heartbeat.touch,
                              aligner or _shared_aligner(workers))

    def await_synth(fut: cf.Future):
        """Wait for a window result, still honouring pause/cancel/supersede promptly."""
        while not cf.wait([fut], timeout=0.25).done:
            checkpoint_job()
        return fut.result()

    try:
        n = len(todo)
        for k, r in enumerate(todo):
            checkpoint_job()
            if clock() - last_renew >= renew_every_s:
                if not rj.renew_lease(book_id, worker_id, now(), lease_s):
                    raise _Stop(LOST)
                last_renew = clock()
            if window is not None:
                window.prepare(k)
            idx = r["index"]
            done = _load_done(tmp, idx)
            if done is None:
                fresh += 1
                fut = window.future(k) if window is not None else None
                timings = None  # set by a window worker that already aligned this sentence
                if fut is not None:
                    res, timings = await_synth(fut)
                else:
                    res = synth(r["text"], voice)
                audio = np.asarray(res.audio, dtype=np.float32)
                if res.effective_speed != 1.0:
                    raise ra.RenderError(
                        f"base effective_speed {res.effective_speed} != 1.0",
                        phase="base_speed_mismatch")
                if audio.size == 0:
                    # Nothing speakable (a lone "-1" or "0" lifted from a figure or
                    # matrix): the engine's G2P yields no phonemes. One such sentence
                    # must not abort a multi-hour render, so give it a short silent
                    # gap with estimated word times; the reader still steps through it.
                    logger.warning("[render] %s sentence %s %r synthesized no audio; "
                                   "using %d ms of silence", book_id, idx, r["text"][:40],
                                   SILENT_SENTENCE_MS)
                    audio = np.zeros(int(SR * SILENT_SENTENCE_MS / 1000), dtype=np.float32)
                    timings = distribute_by_syllables(
                        SILENT_SENTENCE_MS, r["text"].split() or [r["text"]])
                dur_ms = len(audio) * 1000.0 / SR
                if timings is None:
                    timings = align_sentence(audio, SR, r["text"],
                                             native_words=res.native_words,
                                             kokoro_tokens=res.kokoro_tokens, aligner=aligner)
                if not timings:
                    raise ra.RenderError(f"sentence {idx} produced no word entries",
                                         phase="align")
                boxes = ([None] * len(timings) if provider is None
                         else map_words_to_boxes(timings, provider(r)))
                words = _clamp_words([
                    {"word": t.word, "start_ms": t.start_ms, "end_ms": t.end_ms,
                     "box": list(b) if b else None, "timing_source": t.timing_source}
                    for t, b in zip(timings, boxes)], dur_ms)
                # flac first, sidecar last: the sidecar marks the sentence done.
                sf.write(tmp / "chunks" / f"{idx}.flac", audio, SR, format="FLAC",
                         subtype="PCM_16")
                _write_json(tmp / "chunks" / f"{idx}.json", {"words": words})
                audio, _ = sf.read(tmp / "chunks" / f"{idx}.flac", dtype="float32")
            else:
                audio, words = done
            chunks.append({"index": idx, "text": r["text"], "chapter": r.get("chapter", 0),
                           "audio": audio, "words": words})
            progress(0.9 * (k + 1) / n, "synthesize")
            if (k + 1) % STATS_EVERY == 0 or k + 1 == n:
                _stats(book_id, sentences_done=k + 1, sentences_total=n, work_done=fresh,
                       now=time.time())

        if window is not None:
            window.close()
        checkpoint_job()
        phase_stat("encode")
        progress(0.92, "encode")
        rj.renew_lease(book_id, worker_id, now(), lease_s)
        # Encode and audit report inside their phases so the bar moves while they run.
        base = ra.build_base(chunks, tmp, effective_speed=1.0,
                             on_step=lambda f: progress(0.90 + 0.02 * f, "encode"))
        tier_results = ra.build_tiers(base["path"], base["segments"], tiers, tmp,
                                      on_step=lambda f: progress(0.92 + 0.03 * f, "encode"))
        phase_stat("drift_audit")
        progress(0.95, "drift_audit")
        ra.drift_audit(base["path"], base["segments"], tier_results, tmp,
                       on_step=lambda f: progress(0.95 + 0.03 * f, "drift_audit"))
        manifest = ra.write_manifest(
            tmp, base_segments=base["segments"], tier_results=tier_results,
            duration_ms=base["duration_ms"], voice=voice,
            voice_file_sha256=book_processor._voice_hash(voice, None),
            fingerprint=target, base_effective_speed=1.0,
            mixed_backends=synth.mixed)
        del manifest
        for p in tmp.glob("audit_*"):
            p.unlink(missing_ok=True)

        old_rows = db_sentences(book_id)
        remap = {i: v for i, v in enumerate(compute_remap(old_rows, rows))}
        checkpoint_job()
        phase_stat("publish")
        progress(0.98, "publish")
        _publish(book_id, tmp, rows, remap, target, fail_at)
    finally:
        if window is not None:
            window.close()
        closer = getattr(synthesizer, "close_workers", None)
        if callable(closer):
            closer()  # frees the GPU worker processes' VRAM
        close = getattr(provider, "close", None)
        if close:
            close()
    phase_stat("done")
    rj.release(book_id, worker_id)
    return READY


def _publish(book_id, tmp, rows, remap, target, fail_at) -> None:
    with Session(_db.engine) as s:
        b = s.get(Book, book_id)
        if b is None:
            raise _Stop(GONE)
        nxt = (b.ready_generation or 0) + 1
    final = storage.RENDERS_DIR / book_id / str(nxt)
    if final.exists():  # unreferenced leftover of a crash between rename and commit
        shutil.rmtree(final, ignore_errors=True)
    try:
        rs.publish_generation(book_id, tmp, rows, remap, target, fail_at=fail_at)
    except Exception:
        # Failed after the rename but before the commit: hand the directory back
        # as the resumable tmp dir; nothing committed refers to `final`.
        b = _load_book(book_id)
        if b is not None and (b.ready_generation or 0) + 1 == nxt and final.exists() \
                and not tmp.exists():
            try:
                os.rename(final, tmp)
            except OSError:
                pass
        raise
    # Resume state is only needed until publish; keep it out of the render's disk footprint.
    shutil.rmtree(final / "chunks", ignore_errors=True)
    (final / "checkpoint.json").unlink(missing_ok=True)


# ---------------------------------------------------------------------- loop
def processor_enabled() -> bool:
    return os.environ.get("PROCESSOR_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def run_once(worker_id: str, *, synthesizer=None, now: Optional[Callable[[], datetime]] = None,
             debounce_s: int = 60, lease_s: int = 120, **kw) -> Optional[tuple[str, str]]:
    """Claim and render at most one book. Returns (book_id, outcome) or None."""
    clock_now = (now or _utcnow)()
    bid = rj.claim_next(worker_id, clock_now, lease_s=lease_s, debounce_s=debounce_s)
    if bid is None:
        return None
    if synthesizer is None:
        synthesizer = engine_synthesizer()
    return bid, render_book(bid, worker_id, synthesizer=synthesizer, now=now,
                            lease_s=lease_s, **kw)


def worker_loop(stop_event: threading.Event, poll_s: float = 2.0, *, worker_id: Optional[str] = None,
                synthesizer=None, aligner=None, **kw) -> None:
    """Claim -> render -> repeat until ``stop_event``. Never dies on a job error:
    render errors are recorded on the book; anything else is logged and retried."""
    worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    while not stop_event.is_set():
        did = None
        try:
            if processor_enabled() and not render_pause.is_paused() \
                    and (synthesizer is not None or _engine_ready()):
                did = run_once(worker_id, synthesizer=synthesizer, aligner=aligner, **kw)
        except Exception:  # noqa: BLE001 - keep the thread alive
            logger.exception("[render] worker iteration failed")
        if did is None:
            stop_event.wait(poll_s)


def _engine_ready() -> bool:
    from services import tts_engine
    return tts_engine._render_engine is not None


_thread: Optional[threading.Thread] = None
_stop: Optional[threading.Event] = None


def start_worker(poll_s: float = 2.0, **kw) -> Optional[threading.Thread]:
    """Start the background worker (idempotent). No-op if PROCESSOR_ENABLED is off."""
    global _thread, _stop
    if not processor_enabled():
        logger.info("[render] PROCESSOR_ENABLED is off; worker not started")
        return None
    if _thread is not None and _thread.is_alive():
        return _thread
    _stop = threading.Event()
    _thread = threading.Thread(target=worker_loop, args=(_stop, poll_s), kwargs=kw,
                               name="render-worker", daemon=True)
    _thread.start()
    return _thread


def stop_worker(timeout: float = 10.0) -> None:
    global _thread, _stop
    if _stop is not None:
        _stop.set()
    if _thread is not None:
        _thread.join(timeout)
    _thread = _stop = None
