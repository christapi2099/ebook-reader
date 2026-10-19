"""Precompiled chapter audio: build, poll, and serve Opus + sync manifest.

Companion to ``routers/tts.py``'s live WebSocket streaming, not a replacement
for it. A chapter plays over the live path until this router's background job
finishes for it; see ``routers/tts.py``'s module docstring for the other half
of that story. The actual synthesis/atempo logic lives in
``services/precompile_engine.py`` — this module is job bookkeeping and HTTP
surface only, following the same shape as ``routers/mp3.py``'s export job
(``create_export``/``_run_export``/``_run_export_blocking``).

Precompile jobs run through a single global FIFO queue (`_job_queue`), one
chapter at a time, rather than each `POST .../build` spawning its own task.
This exists because of a real failure hit manually testing this feature: two
chapters precompiling concurrently on a 4 GB GPU produced a CUDA OOM (the
live WebSocket path shares the same GPU too, but that contention is not new
here and is out of scope for this queue — see the module docstring in
`routers/tts.py`). Queuing precompile jobs specifically is enough to fix the
failure actually observed: multiple *precompile* jobs piling onto the GPU at
once, which happens easily since a build is auto-triggered on every
never-before-opened chapter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

import db.database as _db
from db.models import ChapterAudio
from routers.deps import require_book
from services import export_encoding, precompile_engine
from services.precompile_engine import NATIVE_SPEED
from services.tts_engine import TTSEngine, normalize_speed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/precompiled")

_kokoro = None
# Shared with the live playback/export engines' pattern: one TTSEngine per
# process, so this job shares the speed-capability probe and the on-disk
# AudioCache with everything else instead of maintaining its own copies.
_engine: TTSEngine | None = None

JobKey = tuple[str, int, str]  # (book_id, chapter, voice)


@dataclass
class _QueuedJob:
    key: JobKey
    book_id: str
    chapter: int
    voice: str
    row_id_native: int
    native_needs_work: bool
    atempo_row_ids_to_build: dict[float, int]


# All access to these three happens on the event loop thread only (enqueue
# from a request handler, dequeue/pop from the worker coroutine below) — both
# run as asyncio tasks on the same loop, so no lock is needed despite the
# worker awaiting across the blocking synthesis call.
_job_queue: "asyncio.Queue[_QueuedJob]" = asyncio.Queue()
_queue_order: list[JobKey] = []  # FIFO order, index 0 is the currently-running job
_worker_task: asyncio.Task | None = None

# In-process zombie-job detection (see _stall_watchdog docstring). Only ever
# describes the job currently at _queue_order[0] — the single-worker queue
# guarantees at most one job is genuinely running at a time.
_running_job_key: JobKey | None = None
_running_job_last_progress: float = 0.0
STALL_TIMEOUT_S = 600.0  # no progress write in 10 minutes == stalled
WATCHDOG_INTERVAL_S = 60.0
_watchdog_task: asyncio.Task | None = None


def queue_position(key: JobKey) -> int | None:
    """0 = running now, 1 = next, ... None = not queued at all."""
    try:
        return _queue_order.index(key)
    except ValueError:
        return None


async def _queue_worker() -> None:
    """Run queued precompile jobs one at a time, forever.

    Started once at app startup (`main.py`'s lifespan) and cancelled at
    shutdown — the same lifecycle as `services/audio_cache.py`'s periodic
    sweep task.
    """
    global _running_job_key, _running_job_last_progress
    while True:
        job = await _job_queue.get()
        _running_job_key = job.key
        _running_job_last_progress = time.monotonic()
        try:
            await run_in_threadpool(
                _run_precompile_blocking, job.row_id_native, job.native_needs_work,
                job.atempo_row_ids_to_build, job.book_id, job.chapter, job.voice,
            )
        except Exception:
            logger.warning("Precompile job %s failed unexpectedly", job.key, exc_info=True)
        finally:
            _running_job_key = None
            if _queue_order and _queue_order[0] == job.key:
                _queue_order.pop(0)
            _job_queue.task_done()


def _reset_orphaned_jobs() -> None:
    """Reset any ``ChapterAudio`` row still ``processing`` from a run that no
    longer exists.

    The in-memory queue (`_job_queue`/`_queue_order`) is always empty right
    after a fresh worker starts — nothing survives a restart. But a row's DB
    status does survive: if the previous process was killed (not stopped
    cleanly) while a job was mid-synthesis, that row is frozen at
    ``status="processing"`` forever, because ``run_precompile_blocking`` only
    ever marks a row ``error`` from inside an ``except`` block, which a hard
    kill never reaches. Left alone, a stuck ``processing`` row can never be
    rebuilt again: ``build_chapter_audio`` treats ``processing`` as "already
    being worked on" and silently skips it on every future request for that
    tier. Resetting it to ``pending`` here makes it look exactly like a tier
    that was created but never started — which, from this fresh process's
    point of view, it always was.
    """
    with Session(_db.engine) as session:
        rows = session.exec(select(ChapterAudio).where(ChapterAudio.status == "processing")).all()
        for row in rows:
            row.status = "pending"
            row.phase = None
            row.progress = 0
            row.error_message = None
        if rows:
            session.commit()
            logger.info("Reset %d orphaned ChapterAudio row(s) from a previous run", len(rows))


def start_queue_worker() -> asyncio.Task:
    """Start (or restart) the queue worker, bound to whatever loop is running now.

    ``asyncio.Queue`` binds to the first loop that calls ``get``/``put`` on it
    and raises if a later call comes from a different loop — harmless for the
    single continuous app lifespan this runs under in production, but fatal
    across the backend test suite, where every test's ``TestClient`` gets its
    own fresh loop: the worker's `await _job_queue.get()` would bind to test
    N's loop, that loop closes when the test ends, and test N+1's *new*
    worker task crashes immediately trying to `get()` from a queue bound to a
    dead loop — silently, since nothing awaits a bare `asyncio.create_task`
    result, so a build request just queues forever with nothing to dequeue it
    (caught here after every subsequent build in the suite burned its full
    poll timeout waiting). Recreating the queue here, every time a *fresh*
    worker actually starts, closes that gap: a fresh queue always matches the
    loop the fresh worker is about to run on. Safe in production too — this
    only ever runs once, at real startup, before anything could be queued.
    """
    global _worker_task, _job_queue
    if _worker_task is None or _worker_task.done():
        _job_queue = asyncio.Queue()
        _queue_order.clear()
        _reset_orphaned_jobs()
        _worker_task = asyncio.create_task(_queue_worker())
    return _worker_task


async def stop_queue_worker() -> None:
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None


def _mark_running_job_stalled(key: JobKey, idle_for: float) -> None:
    """Error out every ``processing``/``pending`` row of the stalled job.

    This makes the stall visible (an ``error`` status the frontend already
    knows how to show, instead of an infinite "Preparing…" spinner with no
    signal anywhere) — it does **not** free up `_synthesis_pool`'s one
    worker thread, which Python has no way to force off a call that is
    genuinely hung deep in Kokoro/Piper/CUDA. If the stall is real (not just
    an unusually large chapter), live playback and every future precompile
    job share that same stuck thread and stay blocked until the backend
    process is restarted — this only stops the wait from being silent.
    """
    book_id, chapter, voice = key
    with Session(_db.engine) as session:
        rows = session.exec(
            select(ChapterAudio).where(
                ChapterAudio.book_id == book_id,
                ChapterAudio.chapter == chapter,
                ChapterAudio.voice == voice,
                ChapterAudio.status.in_(["processing", "pending"]),
            )
        ).all()
        for row in rows:
            row.status = "error"
            row.phase = "error"
            row.error_message = (
                f"Synthesis stalled — no progress for over {int(idle_for)}s. "
                "The backend may need to be restarted to fully recover."
            )
        if rows:
            session.commit()
    logger.error(
        "Precompile job %s has not reported progress for %.0fs — marked as stalled. "
        "The synthesis worker thread itself cannot be forcibly cancelled; if this is "
        "a genuine hang (not just a very large chapter), live playback and any future "
        "precompile job share the same stuck thread until the process is restarted.",
        key, idle_for,
    )


async def _stall_watchdog() -> None:
    """Detect a precompile job that has stopped reporting progress.

    Zombie jobs come in two shapes:
      * the *process* died (killed, crashed) — `_reset_orphaned_jobs` handles
        this, at the next startup, since nothing survives a restart to detect
        it mid-flight.
      * the *job* died but the process is still very much alive — a hung
        ffmpeg subprocess, a wedged CUDA call, anything that blocks the
        single `_synthesis_pool` worker thread forever without raising. This
        task is for that second case: it can't be caught at startup because
        there is no restart, so this polls `_running_job_last_progress` — the
        heartbeat `_update_tier` bumps on every progress write — and flags
        the running job once it's been quiet for `STALL_TIMEOUT_S`.
    """
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_S)
        key = _running_job_key
        if key is None:
            continue
        idle_for = time.monotonic() - _running_job_last_progress
        if idle_for > STALL_TIMEOUT_S:
            _mark_running_job_stalled(key, idle_for)


def start_stall_watchdog() -> asyncio.Task:
    global _watchdog_task
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = asyncio.create_task(_stall_watchdog(), name="precompile-stall-watchdog")
    return _watchdog_task


async def stop_stall_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task is not None:
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        _watchdog_task = None


def set_kokoro(kokoro):
    global _kokoro, _engine
    _kokoro = kokoro
    _engine = TTSEngine(kokoro) if kokoro is not None else None


def _update_tier(row_id: int, **fields) -> None:
    """Write ChapterAudio columns, ignoring a row that has gone away.

    Never raises — mirrors ``routers/mp3.py::_update_export``. This runs on the
    worker thread while a request thread may be reading the same row, and a
    progress-percentage write is never worth failing the whole job over.

    Also the heartbeat for `_stall_watchdog`: every write means the running
    job is still alive and making progress, whatever fields it's writing.
    """
    global _running_job_last_progress
    _running_job_last_progress = time.monotonic()
    try:
        with Session(_db.engine) as session:
            row = session.get(ChapterAudio, row_id)
            if row is None:
                return
            for key, value in fields.items():
                setattr(row, key, value)
            session.commit()
    except Exception:
        logger.warning("Could not record ChapterAudio %s state %s", row_id, fields, exc_info=True)


def _run_precompile_blocking(
    row_id_native: int,
    native_needs_work: bool,
    atempo_row_ids_to_build: dict[float, int],
    book_id: str,
    chapter: int,
    voice: str,
) -> None:
    if _engine is None:
        message = "No TTS engine available"
        if native_needs_work:
            _update_tier(row_id_native, status="error", phase="error", error_message=message)
        for rid in atempo_row_ids_to_build.values():
            _update_tier(rid, status="error", phase="error", error_message=message)
        return

    precompile_engine.run_precompile_blocking(
        _engine, row_id_native, native_needs_work, atempo_row_ids_to_build,
        book_id, chapter, voice, update_row=_update_tier,
    )


class BuildRequest(BaseModel):
    voice: str = "af_heart"
    speeds: list[float] = [0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]


@router.post("/{book_id}/chapters/{chapter}/build")
async def build_chapter_audio(book_id: str, chapter: int, body: BuildRequest):
    # Every tier needs ffmpeg: the native tier for Opus encoding, every atempo
    # tier for both the atempo filter and its own Opus encode. Checked here,
    # not left to fail inside the background job, so the caller gets an
    # immediate, explicit answer instead of a job that starts and then always
    # errors on its first ffmpeg call.
    if not export_encoding.ffmpeg_available():
        raise HTTPException(status_code=503, detail=export_encoding.FFMPEG_MISSING_MESSAGE)

    try:
        speeds = sorted({normalize_speed(s) for s in body.speeds} | {NATIVE_SPEED})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    key = (book_id, chapter, body.voice)
    existing_position = queue_position(key)
    if existing_position is not None:
        # Idempotent: a chapter opened twice in quick succession (e.g. two tabs,
        # or a re-render) must not enqueue a second synthesis job for the same
        # audio — the frontend calls this unconditionally on every chapter open
        # that isn't already `done`.
        return {"status": "already_queued", "queue_position": existing_position}

    with Session(_db.engine) as session:
        require_book(session, book_id)

        row_id_native: int | None = None
        native_needs_work = True
        # Only tiers that actually need synthesizing/stretching are handed to
        # the background job — a row already `done` (or a job already running
        # for it) must not be re-touched. The first attempt at this reused
        # every requested row unconditionally and the job blindly re-ran
        # every tier including already-`done` ones on a second "build" call
        # for the same chapter (caught by an idempotency test), which is
        # exactly the redundant-work/clobber bug a `POST .../build` fired on
        # every chapter-open must never risk.
        atempo_row_ids_to_build: dict[float, int] = {}

        for speed in speeds:
            row = session.exec(
                select(ChapterAudio).where(
                    ChapterAudio.book_id == book_id,
                    ChapterAudio.chapter == chapter,
                    ChapterAudio.voice == body.voice,
                    ChapterAudio.requested_speed == speed,
                )
            ).first()

            if row is not None and row.status in ("done", "processing"):
                if speed == NATIVE_SPEED:
                    row_id_native = row.id
                    native_needs_work = row.status != "done"
                continue  # nothing to build for this tier right now

            if row is None:
                row = ChapterAudio(
                    book_id=book_id, chapter=chapter, voice=body.voice,
                    requested_speed=speed,
                    method="native" if speed == NATIVE_SPEED else "atempo",
                    status="pending", created_at=datetime.now(timezone.utc),
                )
                session.add(row)
            else:
                # Missing/pending, or a prior attempt errored out — (re)try it.
                row.status = "pending"
                row.error_message = None
            session.commit()
            session.refresh(row)

            if speed == NATIVE_SPEED:
                row_id_native = row.id
            else:
                atempo_row_ids_to_build[speed] = row.id

    if row_id_native is None:
        # Unreachable in practice (NATIVE_SPEED is always in `speeds`), but
        # fail loudly rather than pass None into the background job.
        raise HTTPException(status_code=500, detail="Internal error resolving native tier row")

    if not native_needs_work and not atempo_row_ids_to_build:
        return {"status": "already_done"}

    job = _QueuedJob(
        key=key, book_id=book_id, chapter=chapter, voice=body.voice,
        row_id_native=row_id_native, native_needs_work=native_needs_work,
        atempo_row_ids_to_build=atempo_row_ids_to_build,
    )
    _queue_order.append(key)
    await _job_queue.put(job)

    tiers = ([NATIVE_SPEED] if native_needs_work else []) + list(atempo_row_ids_to_build.keys())
    return {"status": "queued", "queue_position": queue_position(key), "tiers": tiers}


@router.get("/{book_id}/chapters/{chapter}/status")
def get_chapter_status(book_id: str, chapter: int, voice: str = "af_heart"):
    with Session(_db.engine) as session:
        rows = session.exec(
            select(ChapterAudio).where(
                ChapterAudio.book_id == book_id,
                ChapterAudio.chapter == chapter,
                ChapterAudio.voice == voice,
            )
        ).all()
        tiers = [
            {
                "requested_speed": r.requested_speed,
                "effective_speed": r.effective_speed,
                "method": r.method,
                "status": r.status,
                "phase": r.phase,
                "progress": r.progress,
                "duration_ms": r.duration_ms,
                "error_message": r.error_message,
            }
            for r in rows
        ]
        return {"tiers": tiers, "queue_position": queue_position((book_id, chapter, voice))}


def _find_done_tier(session: Session, book_id: str, chapter: int, voice: str, speed: float) -> ChapterAudio:
    row = session.exec(
        select(ChapterAudio).where(
            ChapterAudio.book_id == book_id,
            ChapterAudio.chapter == chapter,
            ChapterAudio.voice == voice,
            ChapterAudio.requested_speed == normalize_speed(speed),
        )
    ).first()
    if row is None or row.status != "done" or not row.manifest_path or not row.audio_path:
        raise HTTPException(status_code=404, detail="Chapter audio not ready for this speed")
    return row


@router.get("/{book_id}/chapters/{chapter}/manifest")
def get_chapter_manifest(book_id: str, chapter: int, voice: str = "af_heart", speed: float = 1.0):
    with Session(_db.engine) as session:
        row = _find_done_tier(session, book_id, chapter, voice, speed)
        path = Path(row.manifest_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Manifest file missing on disk")
        return FileResponse(str(path), media_type="application/json")


@router.get("/{book_id}/chapters/{chapter}/audio")
def get_chapter_audio(book_id: str, chapter: int, voice: str = "af_heart", speed: float = 1.0):
    with Session(_db.engine) as session:
        row = _find_done_tier(session, book_id, chapter, voice, speed)
        path = Path(row.audio_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Audio file missing on disk")
        # FileResponse honors Range requests, which <audio> seeking depends on —
        # without it every seek would re-download the whole chapter first.
        return FileResponse(str(path), media_type="audio/ogg")


# TODO(precompiled-audio): invalidate/delete a book's ChapterAudio rows + files
# when its sentences change (re-OCR, re-import). No such hook exists yet —
# needs its own look at services/pdf_engine.py's ingestion path before wiring,
# deliberately deferred rather than guessed at here.
@router.delete("/{book_id}/chapters/{chapter}")
def delete_chapter_audio(book_id: str, chapter: int, voice: str = "af_heart"):
    key = (book_id, chapter, voice)
    # A job still waiting in line (not yet dequeued by the worker) is removed
    # outright — its rows are about to be deleted, so there is nothing left
    # for it to write into. A job already RUNNING is a known, accepted gap
    # (no cancellation exists here, matching MP3Export's own lack of one):
    # it will finish and write to a path/row that may no longer exist, and
    # `_update_tier` already no-ops silently on a missing row.
    if key in _queue_order and _queue_order[0] != key:
        _queue_order.remove(key)

    with Session(_db.engine) as session:
        rows = session.exec(
            select(ChapterAudio).where(
                ChapterAudio.book_id == book_id,
                ChapterAudio.chapter == chapter,
                ChapterAudio.voice == voice,
            )
        ).all()
        for row in rows:
            if row.audio_path:
                Path(row.audio_path).unlink(missing_ok=True)
            if row.manifest_path:
                Path(row.manifest_path).unlink(missing_ok=True)
            session.delete(row)
        session.commit()
    return {"ok": True}
