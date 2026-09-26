from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from db.database import get_session
from db.models import Book, Bookmark, Folder, MP3Export, Progress, Sentence
from routers.deps import require_book

router = APIRouter(prefix="/library")

# Caps for the two user-editable fields, for the same reason folder names are
# capped: both are rendered in a fixed-width card, and the columns are TEXT, so
# the API is the only bound on them.
BOOK_TITLE_MAX_LENGTH = 300
BOOK_AUTHOR_MAX_LENGTH = 200

TITLE_REQUIRED = "Title is required"
TITLE_TOO_LONG = f"Title must be at most {BOOK_TITLE_MAX_LENGTH} characters"
AUTHOR_TOO_LONG = f"Author must be at most {BOOK_AUTHOR_MAX_LENGTH} characters"
NOTHING_TO_UPDATE = "Provide a title or an author to update"


class ProgressUpdate(BaseModel):
    sentence_index: int


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


def _serialize(book: Book, sentence_count: int, sentence_index: int | None) -> dict:
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
            "sentence_index": sentence_index}


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

    return [
        _serialize(b, counts.get(b.id, 0), positions.get(b.id))
        for b in books
    ]


@router.post("/{book_id}/progress")
def update_progress(
    book_id: str,
    body: ProgressUpdate,
    session: Session = Depends(get_session),
):
    require_book(session, book_id)
    row = session.get(Progress, book_id)
    if row:
        row.sentence_index = body.sentence_index
        row.updated_at = datetime.now(timezone.utc)
    else:
        session.add(Progress(
            book_id=book_id,
            sentence_index=body.sentence_index,
            updated_at=datetime.now(timezone.utc),
        ))
    session.commit()
    return {"ok": True}


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
    return _serialize(book, count, row.sentence_index if row else None)


@router.get("/{book_id}")
def get_book(book_id: str, session: Session = Depends(get_session)):
    return _serialize_one(session, require_book(session, book_id))


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

    book.title = title
    book.author = author
    session.add(book)
    session.commit()
    return _serialize_one(session, book)


@router.get("/{book_id}/progress")
def get_progress(book_id: str, session: Session = Depends(get_session)):
    row = session.get(Progress, book_id)
    return {"sentence_index": row.sentence_index if row else 0}


@router.delete("/{book_id}")
def delete_book(book_id: str, session: Session = Depends(get_session)):
    book = require_book(session, book_id)
    for s in session.exec(select(Sentence).where(Sentence.book_id == book_id)):
        session.delete(s)
    for p in session.exec(select(Progress).where(Progress.book_id == book_id)):
        session.delete(p)
    for bm in session.exec(select(Bookmark).where(Bookmark.book_id == book_id)):
        session.delete(bm)
    for ex in session.exec(select(MP3Export).where(MP3Export.book_id == book_id)):
        session.delete(ex)
    session.delete(book)
    session.commit()
    return {"ok": True}
