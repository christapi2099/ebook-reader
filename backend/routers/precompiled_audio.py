"""Precompiled chapter audio: build, poll, and serve Opus + sync manifest.

Companion to ``routers/tts.py``'s live WebSocket streaming, not a replacement
for it. A chapter plays over the live path until this router's background job
finishes for it; see ``routers/tts.py``'s module docstring for the other half
of that story. The actual synthesis/atempo logic lives in
``services/precompile_engine.py`` — this module is job bookkeeping and HTTP
surface only, following the same shape as ``routers/mp3.py``'s export job
(``create_export``/``_run_export``/``_run_export_blocking``).
"""

from __future__ import annotations

import asyncio
import logging
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
_precompile_tasks: dict[tuple[str, int, str], asyncio.Task] = {}


def set_kokoro(kokoro):
    global _kokoro, _engine
    _kokoro = kokoro
    _engine = TTSEngine(kokoro) if kokoro is not None else None


def _update_tier(row_id: int, **fields) -> None:
    """Write ChapterAudio columns, ignoring a row that has gone away.

    Never raises — mirrors ``routers/mp3.py::_update_export``. This runs on the
    worker thread while a request thread may be reading the same row, and a
    progress-percentage write is never worth failing the whole job over.
    """
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


async def _run_precompile(
    key: tuple[str, int, str],
    row_id_native: int,
    native_needs_work: bool,
    atempo_row_ids_to_build: dict[float, int],
    book_id: str,
    chapter: int,
    voice: str,
) -> None:
    """Offload the blocking synthesis/ffmpeg work to a worker thread.

    Same reasoning as ``routers/mp3.py::_run_export``: Kokoro inference and
    ffmpeg are both blocking, and this can run for minutes on a long chapter —
    it must not freeze the event loop (or the live WS playback path) for that
    whole time.
    """
    try:
        await run_in_threadpool(
            _run_precompile_blocking, row_id_native, native_needs_work,
            atempo_row_ids_to_build, book_id, chapter, voice,
        )
    finally:
        _precompile_tasks.pop(key, None)


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
    existing_task = _precompile_tasks.get(key)
    if existing_task is not None and not existing_task.done():
        # Idempotent: a chapter opened twice in quick succession (e.g. two tabs,
        # or a re-render) must not spawn a second synthesis job for the same
        # audio — the frontend calls this unconditionally on every chapter open
        # that isn't already `done`.
        return {"status": "already_running"}

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

    task = asyncio.create_task(
        _run_precompile(
            key, row_id_native, native_needs_work, atempo_row_ids_to_build,
            book_id, chapter, body.voice,
        )
    )
    _precompile_tasks[key] = task

    tiers = ([NATIVE_SPEED] if native_needs_work else []) + list(atempo_row_ids_to_build.keys())
    return {"status": "started", "tiers": tiers}


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
        return [
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
