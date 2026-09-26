import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

import db.database as _db
from db.models import Book, MP3Export, Sentence
from services.tts_engine import TTSEngine, normalize_speed, synthesize_serialized

EXPORTS_DIR = Path("exports")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mp3")

_kokoro = None
# One engine for the whole export path, so exports share the speed-capability
# probe and the on-disk AudioCache with playback instead of maintaining their own
# copies of both.
_engine: TTSEngine | None = None
_export_tasks: dict[int, asyncio.Task] = {}


def set_kokoro(kokoro):
    global _kokoro, _engine
    _kokoro = kokoro
    _engine = TTSEngine(kokoro) if kokoro is not None else None


def _run_export_blocking(export_id: int, book_id: str, voice: str, speed: float) -> None:
    """Synthesize every sentence, write the MP3, update the DB.

    Deliberately synchronous: SQLite access, Kokoro inference and soundfile are
    all blocking calls. This is only ever reached through _run_export(), which
    offloads it to a worker thread so the event loop keeps serving requests.
    """
    with Session(_db.engine) as session:
        export = session.get(MP3Export, export_id)
        if not export:
            return
        export.status = "processing"
        session.commit()

    try:
        rows = _load_sentences(book_id)
        if rows is None:
            raise ValueError("Book not found")

        total = len(rows)
        audio_parts: list[np.ndarray] = []
        rendered_speed: float | None = None

        for idx, (s_idx, s) in enumerate(sorted(rows.items())):
            if s["filtered"]:
                continue

            audio, used_speed = _synthesize(s["text"], voice, speed)
            if rendered_speed is None:
                rendered_speed = used_speed
            elif used_speed != rendered_speed:
                logger.warning(
                    "Export %s rendered sentence %s at %sx after %sx",
                    export_id, s_idx, used_speed, rendered_speed,
                )
            if audio is not None and len(audio) > 0:
                audio_parts.append(audio)

            progress = int((idx + 1) / total * 100)
            with Session(_db.engine) as session:
                ex = session.get(MP3Export, export_id)
                if ex:
                    ex.progress = progress
                    session.commit()

        if not audio_parts:
            raise ValueError("No audio generated for any sentence")

        full_audio = np.concatenate(audio_parts)
        EXPORTS_DIR.mkdir(exist_ok=True)
        file_path = EXPORTS_DIR / f"export_{export_id}_{book_id[:12]}.mp3"
        sf.write(str(file_path), full_audio, 24000, format="MP3")
        file_size = file_path.stat().st_size

        with Session(_db.engine) as session:
            ex = session.get(MP3Export, export_id)
            if ex:
                ex.status = "done"
                ex.progress = 100
                ex.file_path = str(file_path)
                ex.file_size = file_size
                # Record what the file actually is, not what was asked for.
                ex.effective_speed = rendered_speed if rendered_speed is not None else speed
                session.commit()

    except Exception as e:
        with Session(_db.engine) as session:
            ex = session.get(MP3Export, export_id)
            if ex:
                ex.status = "error"
                ex.error_message = str(e)
                session.commit()


async def _run_export(export_id: int, book_id: str, voice: str, speed: float) -> None:
    """Run the export on a worker thread so the event loop is never blocked.

    This used to be `async def` with zero await points, so a single export
    (minutes of synthesis for a real book) froze every other request and the TTS
    WebSocket for its whole duration. The synthesis logic itself is unchanged;
    only the thread it runs on moved. The bookkeeping pop stays on the event
    loop thread, where the task registry is mutated everywhere else.
    """
    try:
        await run_in_threadpool(_run_export_blocking, export_id, book_id, voice, speed)
    finally:
        _export_tasks.pop(export_id, None)


def _load_sentences(book_id: str) -> dict[int, dict] | None:
    with Session(_db.engine) as session:
        book = session.get(Book, book_id)
        if not book:
            return None
        rows = session.exec(
            select(Sentence).where(Sentence.book_id == book_id).order_by(Sentence.index)
        ).all()
        return {s.index: {"text": s.text, "filtered": s.filtered} for s in rows}


def _synthesize(text: str, voice: str, speed: float) -> tuple[np.ndarray | None, float]:
    """Synthesize one sentence through the shared engine and cache.

    Returns ``(audio, effective_speed)``. This used to duplicate the Kokoro call
    and the speed fallback ladder locally and bypass AudioCache entirely, so an
    export re-rendered audio the reader already had, and recorded the *requested*
    speed on the export row even when the installed build could not honour it —
    a permanently wrong label on a file. Both now come from the one engine.
    """
    if _engine is None:
        return None, speed
    return synthesize_serialized(_engine, text, voice, speed)


class ExportRequest(BaseModel):
    book_id: str
    voice: str = "af_heart"
    speed: float = 1.0


@router.post("/export")
async def create_export(body: ExportRequest):
    # Validated here, not deep inside the export loop: an unconstrained speed used
    # to reach Kokoro, where a non-positive or non-finite value fails during
    # iteration outside the guarded call and kills the export with an opaque error
    # (or, worse, claims a rate the file will not have).
    try:
        speed = normalize_speed(body.speed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with Session(_db.engine) as session:
        book = session.get(Book, body.book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")
        export = MP3Export(
            book_id=body.book_id,
            voice=body.voice,
            speed=speed,
            status="pending",
            progress=0,
            created_at=datetime.now(timezone.utc),
        )
        session.add(export)
        session.commit()
        session.refresh(export)
        export_id = export.id

    task = asyncio.create_task(_run_export(export_id, body.book_id, body.voice, speed))
    _export_tasks[export_id] = task

    return {"export_id": export_id}


@router.get("/exports")
def list_exports():
    with Session(_db.engine) as session:
        exports = session.exec(select(MP3Export).order_by(MP3Export.created_at.desc())).all()
        result = []
        for ex in exports:
            book = session.get(Book, ex.book_id)
            result.append({
                "id": ex.id,
                "book_id": ex.book_id,
                "book_title": book.title if book else "Unknown",
                "voice": ex.voice,
                "speed": ex.speed,
                "effective_speed": ex.effective_speed,
                "status": ex.status,
                "progress": ex.progress,
                "file_size": ex.file_size,
                "error_message": ex.error_message,
                "created_at": ex.created_at.isoformat(),
            })
        return result


@router.get("/exports/{export_id}/status")
def get_export_status(export_id: int):
    with Session(_db.engine) as session:
        ex = session.get(MP3Export, export_id)
        if not ex:
            raise HTTPException(status_code=404, detail="Export not found")
        return {
            "status": ex.status,
            "progress": ex.progress,
            "file_size": ex.file_size,
            "error_message": ex.error_message,
            "effective_speed": ex.effective_speed,
        }


@router.get("/downloads/{export_id}")
def download_export(export_id: int):
    with Session(_db.engine) as session:
        ex = session.get(MP3Export, export_id)
        if not ex or ex.status != "done" or not ex.file_path:
            raise HTTPException(status_code=404, detail="Export not ready or not found")
        path = Path(ex.file_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")
        return FileResponse(str(path), media_type="audio/mpeg", filename=path.name)


@router.delete("/exports/{export_id}")
def delete_export(export_id: int):
    with Session(_db.engine) as session:
        ex = session.get(MP3Export, export_id)
        if not ex:
            raise HTTPException(status_code=404, detail="Export not found")
        if ex.file_path:
            Path(ex.file_path).unlink(missing_ok=True)
        session.delete(ex)
        session.commit()
    _export_tasks.pop(export_id, None)
    return {"ok": True}
