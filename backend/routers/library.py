from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from db.database import get_session
from db.models import Book, Progress

router = APIRouter(prefix="/library")


class ProgressUpdate(BaseModel):
    sentence_index: int


@router.get("")
def list_books(session: Session = Depends(get_session)):
    books = session.exec(select(Book)).all()
    return [
        {"id": b.id, "title": b.title, "author": b.author,
         "file_type": b.file_type, "page_count": b.page_count,
         "created_at": b.created_at, "last_opened": b.last_opened}
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


@router.get("/{book_id}/progress")
def get_progress(book_id: str, session: Session = Depends(get_session)):
    row = session.get(Progress, book_id)
    return {"sentence_index": row.sentence_index if row else 0}
