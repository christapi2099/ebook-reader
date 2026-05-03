import asyncio
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

import db.database as _db
from db.models import Book, MP3Export, Sentence

EXPORTS_DIR = Path("exports")

router = APIRouter(prefix="/mp3")

_kokoro = None
_export_tasks: dict[int, asyncio.Task] = {}


def set_kokoro(kokoro):
    global _kokoro
    _kokoro = kokoro


async def _run_export(export_id: int, book_id: str, voice: str, speed: float):
    """Background task: synthesize all sentences, write MP3, update DB."""
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

        for idx, (s_idx, s) in enumerate(sorted(rows.items())):
            if s["filtered"]:
                continue

            audio = _synthesize(s["text"], voice, speed)
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
                session.commit()

    except Exception as e:
        with Session(_db.engine) as session:
            ex = session.get(MP3Export, export_id)
            if ex:
                ex.status = "error"
                ex.error_message = str(e)
                session.commit()
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


def _synthesize(text: str, voice: str, speed: float) -> np.ndarray | None:
    if _kokoro is None:
        return None
    try:
        results = _kokoro(text, voice=voice, speed=speed)
    except TypeError:
        try:
            results = _kokoro(text, voice=voice)
        except TypeError:
            results = _kokoro(text)

    parts = []
    for result in results:
        audio = result[-1]
        parts.append(audio if isinstance(audio, np.ndarray) else np.array(audio))
    return np.concatenate(parts) if parts else None


class ExportRequest(BaseModel):
    book_id: str
    voice: str = "af_heart"
    speed: float = 1.0


@router.post("/export")
async def create_export(body: ExportRequest):
    with Session(_db.engine) as session:
        book = session.get(Book, body.book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")
        export = MP3Export(
            book_id=body.book_id,
            voice=body.voice,
            speed=body.speed,
            status="pending",
            progress=0,
            created_at=datetime.now(timezone.utc),
        )
        session.add(export)
        session.commit()
        session.refresh(export)
        export_id = export.id

    task = asyncio.create_task(_run_export(export_id, body.book_id, body.voice, body.speed))
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
