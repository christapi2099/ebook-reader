import asyncio
import json
import logging
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

import db.database as _db
from db.models import Book, MP3Export
from services import engine_manager, export_batches, export_encoding, modal_remote
from services.export_batches import ExportBatch
from services.sentence_source import load_sentences
from services.tts_engine import TTSEngine, normalize_speed, synthesize_serialized

EXPORTS_DIR = Path("exports")

# How often the Modal phase watcher asks the stats API whether a container is up.
PHASE_POLL_SECONDS = 1.0

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


@dataclass
class ExportOptions:
    """The validated request, in the form the worker thread needs it."""

    format: str = export_encoding.DEFAULT_FORMAT
    bitrate_kbps: int | None = None
    chapters: bool = True
    split_by_chapter: bool = False
    embed_metadata: bool = True

    def as_json(self) -> str:
        return json.dumps(
            {
                "chapters": self.chapters,
                "split_by_chapter": self.split_by_chapter,
                "embed_metadata": self.embed_metadata,
            }
        )


class _ExportState:
    """Phase bookkeeping for one running export.

    Phase changes are the only thing the UI polls for, so they are written
    through to the row immediately and only when they actually change — a
    watcher thread polling once a second must not turn into a second a second of
    SQLite writes.
    """

    def __init__(self, export_id: int) -> None:
        self.export_id = export_id
        # None, not a phase: the first set_phase() must always reach the row,
        # otherwise an export that fails before its first transition would keep
        # whatever the request wrote.
        self.phase: str | None = None
        self._lock = threading.Lock()

    def set_phase(self, phase: str, **fields) -> None:
        with self._lock:
            if phase == self.phase and not fields:
                return
            self.phase = phase
        _update_export(self.export_id, phase=phase, **fields)


def _update_export(export_id: int, **fields) -> None:
    """Write export columns, ignoring a row that has gone away.

    Never raises. This runs on the export worker thread while the request thread
    may be reading the same database, and a bookkeeping write (a progress
    percentage, a phase name) is never worth failing an export over.
    """
    try:
        with Session(_db.engine) as session:
            export = session.get(MP3Export, export_id)
            if export is None:
                return
            for key, value in fields.items():
                setattr(export, key, value)
            session.commit()
    except Exception:
        logger.warning("Could not record export %s state %s", export_id, fields, exc_info=True)


def _run_export_blocking(
    export_id: int,
    book_id: str,
    voice: str,
    speed: float,
    options: ExportOptions,
) -> None:
    """Synthesize every sentence, write the file, update the DB.

    Deliberately synchronous: SQLite access, Kokoro inference and ffmpeg are all
    blocking calls. This is only ever reached through _run_export(), which
    offloads it to a worker thread so the event loop keeps serving requests.
    """
    state = _ExportState(export_id)
    _update_export(export_id, status="processing")

    try:
        with Session(_db.engine) as session:
            book = session.get(Book, book_id)
            if not book:
                raise ValueError("Book not found")
            title, author, cover_page = book.title, book.author, book.cover_page
            source_path = book.file_path

        rows = load_sentences(book_id)
        if rows is None:
            raise ValueError("Book not found")

        client = _modal_client()
        rendered_speed: float | None = None
        if client is not None:
            parts, offsets, batches_total = _render_with_modal(
                state, rows, voice, speed, client
            )
        else:
            parts, offsets, batches_total, rendered_speed = _render_sequentially(
                state, rows, voice, speed
            )

        if not parts:
            raise ValueError("No audio generated for any sentence")

        state.set_phase(engine_manager.PHASE_ENCODING, batches_total=batches_total)
        audio = np.concatenate(parts)

        chapters = (
            export_encoding.chapter_marks(offsets, rows) if options.chapters else []
        )
        metadata = (
            export_encoding.build_ffmetadata(title, author, chapters)
            if options.embed_metadata and (title or author or chapters)
            else None
        )
        cover = (
            _cover_bytes(source_path, cover_page) if options.embed_metadata else None
        )

        EXPORTS_DIR.mkdir(exist_ok=True)
        spec = export_encoding.FORMATS[options.format]
        if options.split_by_chapter and chapters:
            file_path = _write_chapter_files(
                export_id, book_id, audio, offsets, rows, chapters, options, title, author, cover
            )
        else:
            file_path = EXPORTS_DIR / f"export_{export_id}_{book_id[:12]}.{spec.extension}"
            export_encoding.encode(
                audio,
                file_path,
                fmt=options.format,
                bitrate_kbps=options.bitrate_kbps,
                metadata=metadata,
                cover=cover,
            )
        file_size = file_path.stat().st_size

        with Session(_db.engine) as session:
            ex = session.get(MP3Export, export_id)
            if ex:
                ex.status = "done"
                ex.progress = 100
                ex.phase = "complete"
                ex.file_path = str(file_path)
                ex.file_size = file_size
                ex.batches_done = batches_total
                ex.batches_total = batches_total
                # Record what the file actually is, not what was asked for.
                ex.effective_speed = rendered_speed if rendered_speed is not None else speed
                session.commit()

    except Exception as e:
        # Name the stage that failed, per the handoff's error-copy rule: "Failed
        # at extracting text · No text layer found" is useful, "something went
        # wrong" is not. The phase is the only part of that a caller cannot infer.
        message = f"{engine_manager.phase_failure_message(state.phase, e)}"
        logger.warning("Export %s failed: %s", export_id, message, exc_info=True)
        _update_export(export_id, status="error", phase="error", error_message=message)


def _modal_client() -> modal_remote.ModalKokoroClient | None:
    """The live engine, when it is the Modal client (and therefore batchable)."""
    if _engine is None:
        return None
    kokoro = _engine.kokoro
    return kokoro if isinstance(kokoro, modal_remote.ModalKokoroClient) else None


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _render_with_modal(
    state: _ExportState,
    rows: dict[int, dict],
    voice: str,
    speed: float,
    client: modal_remote.ModalKokoroClient,
) -> tuple[list[np.ndarray], dict[int, tuple[int, int]], int]:
    """Fan the book out over Modal containers, one batch per container.

    Returns the audio parts in sentence order, each sentence's sample offset in
    the assembled track (which is what chapter marks are built from), and the
    batch count.
    """
    batches = export_batches.plan_batches(rows)
    if not batches:
        return [], {}, 0

    state.set_phase(engine_manager.PHASE_UPLOADING, batches_total=len(batches))

    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch_modal_phase, args=(state, client, stop), daemon=True
    )
    watcher.start()

    finished: dict[int, modal_remote.BatchAudio] = {}
    try:
        for done, batch in enumerate(
            client.synthesize_batches(
                [b.as_payload() for b in batches], voice=voice, speed=speed
            ),
            start=1,
        ):
            finished[batch.batch_index] = batch
            state.set_phase(
                engine_manager.PHASE_PROCESSING,
                batches_done=done,
                progress=int(done / len(batches) * 100),
            )
    except Exception as exc:
        # One retry, because a batch failure is usually a preempted or restarted
        # container rather than bad input; a second failure is reported.
        logger.warning("Modal batch export failed, retrying once: %s", exc)
        finished = _retry_missing_batches(
            state, client, batches, voice, speed, finished, len(batches)
        )
    finally:
        stop.set()

    missing = [b.batch_index for b in batches if b.batch_index not in finished]
    if missing:
        raise RuntimeError(f"Failed at Modal batch {missing[0]}/{len(batches)}")

    parts: list[np.ndarray] = []
    offsets: dict[int, tuple[int, int]] = {}
    cursor = 0
    for batch in batches:
        audio = finished[batch.batch_index].audio
        parts.append(audio)
        lengths = finished[batch.batch_index].sentence_samples
        for position, sentence in enumerate(batch.sentences):
            length = lengths[position] if position < len(lengths) else 0
            if length <= 0:
                continue
            offsets[int(sentence["index"])] = (cursor, cursor + length)
            cursor += length
    return parts, offsets, len(batches)


def _retry_missing_batches(
    state: _ExportState,
    client: modal_remote.ModalKokoroClient,
    batches: list[ExportBatch],
    voice: str,
    speed: float,
    finished: dict[int, modal_remote.BatchAudio],
    total: int,
) -> dict[int, modal_remote.BatchAudio]:
    """Re-run every batch that has not come back yet, once."""
    missing = [b for b in batches if b.batch_index not in finished]
    if not missing:
        return finished
    state.set_phase(engine_manager.PHASE_STARTING)
    for batch in client.synthesize_batches(
        [b.as_payload() for b in missing], voice=voice, speed=speed
    ):
        finished[batch.batch_index] = batch
        state.set_phase(
            engine_manager.PHASE_PROCESSING,
            batches_done=len(finished),
            progress=int(len(finished) / total * 100),
        )
    return finished


def _watch_modal_phase(
    state: _ExportState, client: modal_remote.ModalKokoroClient, stop: threading.Event
) -> None:
    """Say whether the GPU is booting or loading, which the export cannot see.

    Both are long and look identical from the client's side, and both used to be
    an unexplained pause before any progress appeared. The distinction comes from
    the Modal stats API: zero runners means the container is still starting,
    a live runner that has not answered yet means the model is loading.
    """
    while not stop.wait(PHASE_POLL_SECONDS):
        if state.phase != engine_manager.PHASE_STARTING:
            return
        runners = client.runner_count()
        if runners:
            state.set_phase(engine_manager.PHASE_WARMING_UP)
            return


def _render_sequentially(
    state: _ExportState,
    rows: dict[int, dict],
    voice: str,
    speed: float,
) -> tuple[list[np.ndarray], dict[int, tuple[int, int]], int, float | None]:
    """The original one-sentence-at-a-time path, used on local engines.

    Returns the parts, the per-sentence offsets, zero batches, and the speed the
    audio was really rendered at (which can differ from the request on a Kokoro
    build that rejects ``speed=``).
    """
    state.set_phase(engine_manager.PHASE_PROCESSING)
    total = len(rows)
    parts: list[np.ndarray] = []
    offsets: dict[int, tuple[int, int]] = {}
    cursor = 0
    rendered_speed: float | None = None

    for idx, (s_idx, s) in enumerate(sorted(rows.items())):
        if s["filtered"]:
            continue

        audio, used_speed = _synthesize(s["text"], voice, speed)
        if rendered_speed is None:
            rendered_speed = used_speed
        elif used_speed != rendered_speed:
            logger.warning(
                "Export rendered sentence %s at %sx after %sx",
                s_idx, used_speed, rendered_speed,
            )
        if audio is not None and len(audio) > 0:
            parts.append(audio)
            offsets[s_idx] = (cursor, cursor + len(audio))
            cursor += len(audio)

        _update_export(state.export_id, progress=int((idx + 1) / total * 100))

    return parts, offsets, 0, rendered_speed


async def _run_export(
    export_id: int, book_id: str, voice: str, speed: float, options: ExportOptions
) -> None:
    """Run the export on a worker thread so the event loop is never blocked.

    This used to be `async def` with zero await points, so a single export
    (minutes of synthesis for a real book) froze every other request and the TTS
    WebSocket for its whole duration. The synthesis logic itself is unchanged;
    only the thread it runs on moved. The bookkeeping pop stays on the event
    loop thread, where the task registry is mutated everywhere else.
    """
    try:
        await run_in_threadpool(
            _run_export_blocking, export_id, book_id, voice, speed, options
        )
    finally:
        _export_tasks.pop(export_id, None)


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


def _cover_bytes(source_path: str | None, cover_page: int) -> bytes | None:
    """Render the book's cover page to JPEG, or ``None`` if it cannot be done.

    A missing cover must never fail an export that is otherwise fine, so every
    failure here is swallowed and the file is simply written without artwork.
    """
    if not source_path:
        return None
    try:
        import pymupdf

        with pymupdf.open(source_path) as document:
            if document.page_count == 0:
                return None
            page = document[min(max(cover_page, 0), document.page_count - 1)]
            pixmap = page.get_pixmap(dpi=100)
            return pixmap.tobytes("jpeg")
    except Exception:
        logger.info("No cover art for %s", source_path, exc_info=True)
        return None


def _write_chapter_files(
    export_id: int,
    book_id: str,
    audio: np.ndarray,
    offsets: dict[int, tuple[int, int]],
    rows: dict[int, dict],
    chapters: list[dict],
    options: ExportOptions,
    title: str,
    author: str,
    cover: bytes | None,
) -> Path:
    """Write one file per chapter and return the zip that downloads them all."""
    spec = export_encoding.FORMATS[options.format]
    folder = EXPORTS_DIR / f"export_{export_id}_{book_id[:12]}"
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for number, chapter in enumerate(chapters, start=1):
        start = int(chapter["start_ms"] / 1000 * export_encoding.SAMPLE_RATE)
        end = int(chapter["end_ms"] / 1000 * export_encoding.SAMPLE_RATE)
        clip = audio[start:end]
        if clip.size == 0:
            continue
        name = _chapter_filename(number, chapter.get("title"))
        target = folder / f"{name}.{spec.extension}"
        metadata = (
            export_encoding.build_ffmetadata(
                f"{title} — {chapter.get('title') or f'Chapter {number}'}".strip(" —"),
                author,
                [],
            )
            if options.embed_metadata
            else None
        )
        export_encoding.encode(
            clip,
            target,
            fmt=options.format,
            bitrate_kbps=options.bitrate_kbps,
            metadata=metadata,
            cover=cover,
        )
        written.append(target)

    archive = EXPORTS_DIR / f"export_{export_id}_{book_id[:12]}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in written:
            bundle.write(path, arcname=path.name)
    for path in written:
        path.unlink(missing_ok=True)
    folder.rmdir()
    return archive


def _chapter_filename(number: int, chapter_title: str | None) -> str:
    """``01 - Chapter name`` with anything a filesystem would object to removed."""
    safe = "".join(c for c in (chapter_title or "") if c.isalnum() or c in " -_").strip()
    safe = " ".join(safe.split())[:60]
    return f"{number:02d} - {safe}" if safe else f"{number:02d}"


class ExportRequest(BaseModel):
    book_id: str
    voice: str = "af_heart"
    speed: float = 1.0
    format: str = export_encoding.DEFAULT_FORMAT
    bitrate_kbps: int | None = None
    chapters: bool = True
    split_by_chapter: bool = False
    embed_metadata: bool = True


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

    try:
        fmt = export_encoding.normalise_format(body.format)
        bitrate = export_encoding.normalise_bitrate(fmt, body.bitrate_kbps)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    spec = export_encoding.FORMATS[fmt]
    if spec.needs_ffmpeg and not export_encoding.ffmpeg_available():
        raise HTTPException(status_code=503, detail=export_encoding.FFMPEG_MISSING_MESSAGE)

    options = ExportOptions(
        format=fmt,
        bitrate_kbps=bitrate,
        chapters=body.chapters and spec.supports_metadata,
        split_by_chapter=body.split_by_chapter,
        embed_metadata=body.embed_metadata and spec.supports_metadata,
    )

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
            # A local export never has an invisible GPU start-up, so it must not
            # claim to be starting one: only the Modal path can be "starting".
            phase=(
                engine_manager.PHASE_STARTING
                if _modal_client() is not None
                else engine_manager.PHASE_PROCESSING
            ),
            format=fmt,
            bitrate_kbps=bitrate,
            options=options.as_json(),
            created_at=datetime.now(timezone.utc),
        )
        session.add(export)
        session.commit()
        session.refresh(export)
        export_id = export.id

    task = asyncio.create_task(
        _run_export(export_id, body.book_id, body.voice, speed, options)
    )
    _export_tasks[export_id] = task

    return {"export_id": export_id, "format": fmt, "bitrate_kbps": bitrate}


@router.get("/formats")
def list_formats() -> list[dict]:
    """What the export dialog can offer, straight from the encoder's own table.

    Served rather than hardcoded in the UI so a bitrate the backend would reject
    cannot be offered by the dialog, and so the ffmpeg requirement is visible
    before the user picks M4B.
    """
    available = export_encoding.ffmpeg_available()
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "extension": spec.extension,
            "content_type": spec.content_type,
            "bitrates": list(spec.bitrates),
            "default_bitrate": spec.default_bitrate,
            "supports_metadata": spec.supports_metadata,
            "available": available or not spec.needs_ffmpeg,
            "reason": None if available or not spec.needs_ffmpeg else export_encoding.FFMPEG_MISSING_MESSAGE,
        }
        for spec in export_encoding.FORMATS.values()
    ]


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
                "format": ex.format,
                "bitrate_kbps": ex.bitrate_kbps,
                "phase": ex.phase,
                "batches_done": ex.batches_done,
                "batches_total": ex.batches_total,
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
            "phase": ex.phase,
            "batches_done": ex.batches_done,
            "batches_total": ex.batches_total,
            "format": ex.format,
            "bitrate_kbps": ex.bitrate_kbps,
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
        spec = export_encoding.FORMATS.get(ex.format or export_encoding.DEFAULT_FORMAT)
        if path.suffix == ".zip":
            media_type = "application/zip"
        elif spec is not None:
            media_type = spec.content_type
        else:
            media_type = "application/octet-stream"
        return FileResponse(str(path), media_type=media_type, filename=path.name)


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
