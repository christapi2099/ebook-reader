from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from db.database import get_session
from db.models import Book, Bookmark, Folder, MP3Export, Progress, Sentence

router = APIRouter(prefix="/library")


class ProgressUpdate(BaseModel):
    sentence_index: int


class FolderAssignment(BaseModel):
    """`folder_id=None` unfiles the book."""

    folder_id: int | None = None


def _serialize(book: Book) -> dict:
    return {"id": book.id, "title": book.title, "author": book.author,
            "file_type": book.file_type, "page_count": book.page_count,
            "created_at": book.created_at, "last_opened": book.last_opened,
            "folder_id": book.folder_id}


@router.get("")
def list_books(session: Session = Depends(get_session)):
    # Filter out ephemeral text books (unless they were just saved)
    books = session.exec(
        select(Book).where(Book.ephemeral == False)
    ).all()
    return [_serialize(b) for b in books]


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
    return _serialize(book)


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
