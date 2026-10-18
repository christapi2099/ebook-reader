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
from db.models import Book, Sentence
from services import (
    book_processor, render_artifacts as ra, render_jobs as rj, render_store as rs,
)
from services.aligner import align_sentence
from services.remap import compute_remap
from services.render_state import JOB_ERROR, JOB_PENDING
from services.word_boxes import map_words_to_boxes

logger = logging.getLogger(__name__)

SR = ra.SR
LEASE_RENEW_S = 30.0
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
def engine_synthesizer(engine=None, speed: float = 1.0) -> Callable[[str, str], SynthResult]:
    """Adapt ``tts_engine.synthesize_sentence_for_render`` (async, one sentence per
    pool task) to the blocking seam. Carries no tokens, so words are CTC-aligned."""
    import asyncio

    from services import tts_engine

    def _synth(text: str, voice: str) -> SynthResult:
        audio, eff, native, tokens = asyncio.run(
            tts_engine.synthesize_render_result(text, voice, speed, engine=engine))
        if audio is None:
            raise ra.RenderError("synthesis produced no audio", phase="synthesize")
        return SynthResult(np.asarray(audio, dtype=np.float32), eff, native, tokens)

    return _synth


STATS_EVERY = 10


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

    def __call__(self, text: str, voice: str) -> SynthResult:
        if not self.mixed:
            attempts = 1 + (self.retries if self.fallback else 0)
            last: Optional[Exception] = None
            for n in range(attempts):
                try:
                    return self.primary(text, voice)
                except Exception as e:  # noqa: BLE001 - any backend failure is retryable
                    last = e
                    if self.fallback is None:
                        raise
                    if n < attempts - 1:
                        self.sleep(min(2.0 ** n, 30.0))
            logger.warning("[render] primary backend failed %d times (%s); using fallback",
                           attempts, last)
            self.mixed = True
            self.on_fallback()
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
                raise_errors: bool = False) -> str:
    """Render a CLAIMED book. Returns READY | REQUEUED | CANCELLED | GONE | ERROR |
    BLOCKED | LOST. Failures become ``job_status='error'`` (traceback in
    ``error_message``, ``phase`` = the failing phase) unless ``raise_errors``."""
    now = now or _utcnow
    ctx: dict = {}
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

    def checkpoint_job(drop_tmp=False) -> None:
        """Between-step supersede/cancel/delete check."""
        verdict = rj.check_job(book_id, target)
        if verdict == rj.CONTINUE:
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
    try:
        n = len(todo)
        for k, r in enumerate(todo):
            checkpoint_job()
            if clock() - last_renew >= renew_every_s:
                if not rj.renew_lease(book_id, worker_id, now(), lease_s):
                    raise _Stop(LOST)
                last_renew = clock()
            idx = r["index"]
            done = _load_done(tmp, idx)
            if done is None:
                fresh += 1
                res = synth(r["text"], voice)
                audio = np.asarray(res.audio, dtype=np.float32)
                if res.effective_speed != 1.0:
                    raise ra.RenderError(
                        f"base effective_speed {res.effective_speed} != 1.0",
                        phase="base_speed_mismatch")
                if audio.size == 0:
                    raise ra.RenderError(f"sentence {idx} synthesized no audio",
                                         phase="synthesize")
                dur_ms = len(audio) * 1000.0 / SR
                timings = align_sentence(audio, SR, r["text"], native_words=res.native_words,
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

        checkpoint_job()
        phase_stat("encode")
        progress(0.92, "encode")
        rj.renew_lease(book_id, worker_id, now(), lease_s)
        base = ra.build_base(chunks, tmp, effective_speed=1.0)
        tier_results = ra.build_tiers(base["path"], base["segments"], tiers, tmp)
        phase_stat("drift_audit")
        progress(0.96, "drift_audit")
        ra.drift_audit(base["path"], base["segments"], tier_results, tmp)
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
            if processor_enabled() and (synthesizer is not None or _engine_ready()):
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
