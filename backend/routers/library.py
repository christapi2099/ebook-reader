from datetime import datetime, timezone
from pathlib import Path

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, func, select

from db.database import get_session
from storage import RENDERS_DIR
from db.models import Book, Bookmark, Folder, MP3Export, Progress, Sentence, UserSettings
from routers.deps import require_book
from services import render_jobs, render_stats, render_triggers
import storage

router = APIRouter(prefix="/library")

# Caps for the two user-editable fields, for the same reason folder names are
# capped: both are rendered in a fixed-width card, and the columns are TEXT, so
# the API is the only bound on them.
BOOK_TITLE_MAX_LENGTH = 300
BOOK_AUTHOR_MAX_LENGTH = 200

TITLE_REQUIRED = "Title is required"
TITLE_TOO_LONG = f"Title must be at most {BOOK_TITLE_MAX_LENGTH} characters"
AUTHOR_TOO_LONG = f"Author must be at most {BOOK_AUTHOR_MAX_LENGTH} characters"
NOTHING_TO_UPDATE = "Provide a title, an author or a voice to update"


class ProgressUpdate(BaseModel):
    sentence_index: int
    # Render generation the index was computed against. Optional for old clients.
    generation: int | None = None


def translate_index(book_id: str, generation: int | None, index: int,
                    ready_generation: int | None) -> int:
    """Map an index written against an older generation onto the ready one.

    A write is never rejected for being old (a backgrounded tab's pagehide
    beacon must still land on the right text). ``services.remap`` is owned by
    another module and may not exist yet, so it is imported lazily; without
    ``translate_progress_write`` the index is stored as given, at the ready
    generation, until that function lands.
    """
    if generation is None or ready_generation is None or generation >= ready_generation:
        return index
    try:
        from services.remap import translate_progress_write
    except (ImportError, AttributeError):
        return index
    return int(translate_progress_write(book_id, generation, index))


class FolderAssignment(BaseModel):
    """`folder_id=None` unfiles the book."""

    folder_id: int | None = None


class BookMetadata(BaseModel):
    """`PATCH /library/{book_id}` body. Both fields are optional.

    Which fields were actually sent decides what changes, so this model keeps no
    "unset" sentinel of its own: `model_fields_set` already distinguishes
    "absent" from the explicit `null` that clears an author. `author=""` is
    normalised to `None` rather than stored, so no empty string can sit in the
    column looking like a name.
    """

    title: str | None = None
    author: str | None = None
    # Changing the voice requests a background re-render; the book stays
    # readable on its current generation meanwhile.
    voice: str | None = None


VOICE_MAX_LENGTH = 100
VOICE_INVALID = "Voice must be a non-empty string of at most 100 characters"


def _clean_title(raw: str | None) -> str:
    """Validate and normalise a title, or raise 400.

    Whitespace is trimmed rather than rejected, so a title pasted with a stray
    newline still works — the same rule `_clean_name` applies to folder names. An
    all-whitespace title is empty after trimming, and `Book.title` is NOT NULL,
    so it is rejected as blank.
    """
    title = (raw or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail=TITLE_REQUIRED)
    if len(title) > BOOK_TITLE_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=TITLE_TOO_LONG)
    return title


def _clean_voice(raw: str | None) -> str:
    voice = (raw or "").strip()
    if not voice or len(voice) > VOICE_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=VOICE_INVALID)
    return voice


def _clean_author(raw: str | None) -> str | None:
    """Validate and normalise an author, or raise 400.

    `None` and `""` both mean "no author", which is what the column stores.
    """
    author = (raw or "").strip()
    if not author:
        return None
    if len(author) > BOOK_AUTHOR_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=AUTHOR_TOO_LONG)
    return author


def _render_status(book: Book, queue_position: int | None = None) -> dict:
    """Render-state fields shared by the list and detail responses."""
    stats = render_stats.read(book.id) if book.job_status in ("pending", "processing") else {}
    sps, total = stats.get("sps_ewma"), stats.get("sentences_total")
    eta = None
    if book.job_status == "processing" and sps and total:
        eta = max(0, round((1.0 - (book.progress or 0.0)) * total / sps))
    needs = book.ready_generation is None or (
        book.target_fingerprint is not None
        and book.target_fingerprint != book.ready_fingerprint)
    return {"ready_generation": book.ready_generation, "job_status": book.job_status,
            "progress": book.progress, "phase": book.phase,
            "eta_seconds": eta, "queue_position": queue_position,
            "needs_render": needs, "voice": book.voice}


def _queue_positions(session: Session) -> dict[str, int]:
    """1-based position of each waiting/running job, in claim order."""
    rows = session.exec(
        select(Book.id).where(Book.job_status.in_(("pending", "processing")))
        .order_by(Book.render_priority.desc(), Book.created_at.asc())
    ).all()
    return {bid: i + 1 for i, bid in enumerate(rows)}


def _serialize(book: Book, sentence_count: int, sentence_index: int | None,
               queue_position: int | None = None) -> dict:
    """One library row, including the two fields progress needs.

    `sentence_count` and `sentence_index` are the only honest denominator and
    numerator for reading progress. `page_count` is not: it means PDF pages, or a
    sentence count, or a derived ``len(text) // 10`` depending on file_type, so a
    client that divides by it shows a wrong percentage for two of the three
    formats.

    They are emitted here rather than fetched per book because the alternative is
    what the library used to do: pull every started book's FULL sentence list —
    including word bounding boxes it never reads — purely to learn a total. That
    is one request per book on a page that should cost one.
    """
    return {"id": book.id, "title": book.title, "author": book.author,
            "file_type": book.file_type, "page_count": book.page_count,
            "created_at": book.created_at, "last_opened": book.last_opened,
            "folder_id": book.folder_id,
            "sentence_count": sentence_count,
            "sentence_index": sentence_index,
            **_render_status(book, queue_position)}


@router.get("")
def list_books(session: Session = Depends(get_session)):
    # Filter out ephemeral text books (unless they were just saved)
    books = session.exec(
        select(Book).where(Book.ephemeral == False)
    ).all()

    # Two grouped queries covering the whole library, rather than two per book.
    counts = dict(session.exec(
        select(Sentence.book_id, func.count(Sentence.id)).group_by(Sentence.book_id)
    ).all())
    positions = {p.book_id: p.sentence_index for p in session.exec(select(Progress)).all()}
    queue = _queue_positions(session)

    return [
        _serialize(b, counts.get(b.id, 0), positions.get(b.id), queue.get(b.id))
        for b in books
    ]


@router.post("/{book_id}/progress")
def update_progress(
    book_id: str,
    body: ProgressUpdate,
    session: Session = Depends(get_session),
):
    """Store the reading position, clamped into the book's sentence range.

    An out-of-range index (a stale client, a beacon sent for an older import, a
    hand-written request) would otherwise be stored as-is and handed back on the
    next open as a position past the end of the book. Sentences are written in
    the same transaction as the book, so the count is always known here. The
    stored value is returned so the caller can see what was actually kept.
    """
    book = require_book(session, book_id)
    count = session.exec(
        select(func.count(Sentence.id)).where(Sentence.book_id == book_id)
    ).one()
    raw_index = translate_index(book_id, body.generation, body.sentence_index,
                                book.ready_generation)
    sentence_index = max(0, min(raw_index, count - 1))
    row = session.get(Progress, book_id)
    if row:
        row.sentence_index = sentence_index
        row.updated_at = datetime.now(timezone.utc)
        row.generation = book.ready_generation
    else:
        session.add(Progress(
            book_id=book_id,
            sentence_index=sentence_index,
            updated_at=datetime.now(timezone.utc),
            generation=book.ready_generation,
        ))
    session.commit()
    return {"ok": True, "sentence_index": sentence_index}


@router.post("/{book_id}/folder")
def set_book_folder(
    book_id: str,
    body: FolderAssignment,
    session: Session = Depends(get_session),
):
    """File a book into a folder, or clear it by sending `folder_id: null`."""
    book = require_book(session, book_id)
    if body.folder_id is not None and not session.get(Folder, body.folder_id):
        raise HTTPException(status_code=404, detail="Folder not found")
    book.folder_id = body.folder_id
    session.add(book)
    session.commit()
    return {"ok": True, "folder_id": book.folder_id}


def _serialize_one(session: Session, book: Book) -> dict:
    """One book's row, read exactly the way `GET /library/{book_id}` reads it.

    Shared so that a write can answer with the same shape as the read instead of
    the client having to refetch to find out what it now holds.
    """
    count = session.exec(
        select(func.count(Sentence.id)).where(Sentence.book_id == book.id)
    ).one()
    row = session.get(Progress, book.id)
    return _serialize(book, count, row.sentence_index if row else None,
                      _queue_positions(session).get(book.id))


@router.get("/{book_id}")
def get_book(book_id: str, session: Session = Depends(get_session)):
    book = require_book(session, book_id)
    # Opening a book moves its render to the front of the queue.
    render_jobs.bump_priority(book_id)
    session.refresh(book)
    return _serialize_one(session, book)


@router.patch("/{book_id}")
def update_book(
    book_id: str,
    body: BookMetadata | None = None,
    session: Session = Depends(get_session),
):
    """Set a book's title and/or author; omitted fields are left alone.

    Every import starts with `author = NULL`, which is what the card reports as
    "Author not detected" — this endpoint is the only way to give it a value.
    The body is optional in the signature only so that a request carrying nothing
    at all is a 400 instead of FastAPI's 422: either way, a PATCH that would
    change nothing is refused rather than silently accepted.

    Answers with the same serialised row as `GET /library/{book_id}`, so the
    client can trust the response over what it typed.
    """
    book = require_book(session, book_id)
    provided = body.model_fields_set if body is not None else set()
    if not provided:
        raise HTTPException(status_code=400, detail=NOTHING_TO_UPDATE)

    # Both fields are validated before either is applied, so a rejected author
    # cannot leave a silently renamed title behind. A field that was not sent
    # keeps the value it already had.
    title = _clean_title(body.title) if "title" in provided else book.title
    author = _clean_author(body.author) if "author" in provided else book.author
    voice = _clean_voice(body.voice) if "voice" in provided else None

    book.title = title
    book.author = author
    if voice is not None:
        book.voice = voice
    session.add(book)
    session.commit()
    if voice is not None:
        # A voice equal to the ready render's cancels any pending job (flip-back).
        render_triggers.request_for_book(book_id, voice)
        session.refresh(book)
    return _serialize_one(session, book)


@router.get("/{book_id}/progress")
def get_progress(book_id: str, session: Session = Depends(get_session)):
    row = session.get(Progress, book_id)
    return {"sentence_index": row.sentence_index if row else 0}


@router.post("/{book_id}/reprocess")
def reprocess_book(book_id: str, cancel: bool = Query(False),
                   session: Session = Depends(get_session)):
    """Re-render the book now (no debounce), or with ``?cancel=true`` cancel the job."""
    book = require_book(session, book_id)
    if cancel:
        render_jobs.cancel(book_id)
    else:
        render_triggers.request_for_book(book_id, book.voice, debounce=False)
    session.refresh(book)
    return _serialize_one(session, book)


@router.get("/{book_id}/render")
def get_render_manifest(book_id: str, session: Session = Depends(get_session)):
    """The ready generation's manifest.json."""
    book = require_book(session, book_id)
    if book.ready_generation is None:
        raise HTTPException(status_code=409, detail="Book has no ready render")
    path = Path(storage.RENDERS_DIR) / book_id / str(book.ready_generation) / "manifest.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Manifest missing")


@router.get("/{book_id}/processing")
def get_processing(book_id: str, session: Session = Depends(get_session)):
    """Diagnostics for a render job (see services/render_stats.py for the storage)."""
    book = require_book(session, book_id)
    stats = render_stats.read(book_id)
    total = stats.get("sentences_total")
    if total is None:
        total = session.exec(
            select(func.count(Sentence.id)).where(Sentence.book_id == book_id)).one()
    return {
        "job_status": book.job_status, "phase": book.phase, "progress": book.progress,
        "phase_timings": stats.get("phase_timings", {}),
        "last_error": stats.get("last_error") or book.error_message,
        "error_message": book.error_message,
        "sentences_done": stats.get("sentences_done", 0),
        "sentences_total": total,
        "sentences_per_second": stats.get("sps_ewma"),
    }


def _remove_renders(book_id: str) -> None:
    """rmtree RENDERS_DIR/{id}, refusing ids that would escape RENDERS_DIR."""
    import shutil
    root = Path(RENDERS_DIR).resolve()
    target = (root / book_id).resolve()
    if target != root and target.is_relative_to(root):
        shutil.rmtree(target, ignore_errors=True)


@router.delete("/{book_id}")
def delete_book(book_id: str, session: Session = Depends(get_session)):
    book = require_book(session, book_id)
    file_path = book.file_path
    # Children first, then anything that merely references the book, then the
    # book itself, so foreign-key enforcement never sees a dangling reference.
    for model in (Sentence, Progress, Bookmark, MP3Export):
        for row in session.exec(select(model).where(model.book_id == book_id)):
            session.delete(row)
    for settings in session.exec(
        select(UserSettings).where(UserSettings.last_book_id == book_id)
    ):
        settings.last_book_id = None
        session.add(settings)
    session.flush()
    session.delete(book)
    session.commit()
    if file_path:
        Path(file_path).unlink(missing_ok=True)
    _remove_renders(book_id)
    return {"ok": True}
