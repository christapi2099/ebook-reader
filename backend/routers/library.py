from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from db.database import get_session
from db.models import Book, Bookmark, Folder, MP3Export, Progress, Sentence

router = APIRouter(prefix="/library")


class ProgressUpdate(BaseModel):
    sentence_index: int


class FolderAssignment(BaseModel):
    """`folder_id=None` unfiles the book."""

    folder_id: int | None = None


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
    if not session.get(Book, book_id):
        raise HTTPException(status_code=404, detail="Book not found")
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
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if body.folder_id is not None and not session.get(Folder, body.folder_id):
        raise HTTPException(status_code=404, detail="Folder not found")
    book.folder_id = body.folder_id
    session.add(book)
    session.commit()
    return {"ok": True, "folder_id": book.folder_id}


@router.get("/{book_id}")
def get_book(book_id: str, session: Session = Depends(get_session)):
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    count = session.exec(
        select(func.count(Sentence.id)).where(Sentence.book_id == book_id)
    ).one()
    row = session.get(Progress, book_id)
    return _serialize(book, count, row.sentence_index if row else None)


@router.get("/{book_id}/progress")
def get_progress(book_id: str, session: Session = Depends(get_session)):
    row = session.get(Progress, book_id)
    return {"sentence_index": row.sentence_index if row else 0}


@router.delete("/{book_id}")
def delete_book(book_id: str, session: Session = Depends(get_session)):
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
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
