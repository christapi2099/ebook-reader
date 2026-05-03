from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from db.database import get_session
from db.models import Book, Bookmark, Sentence

router = APIRouter(prefix="/bookmarks")


class BookmarkCreate(BaseModel):
    book_id: str
    sentence_index: int
    label: str


@router.post("")
def create_bookmark(body: BookmarkCreate, session: Session = Depends(get_session)):
    book = session.get(Book, body.book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Resolve page from sentence
    sentence = session.exec(
        select(Sentence).where(
            Sentence.book_id == body.book_id,
            Sentence.index == body.sentence_index,
        )
    ).first()
    page = sentence.page if sentence else 0

    bm = Bookmark(
        book_id=body.book_id,
        sentence_index=body.sentence_index,
        page=page,
        label=body.label[:100],
        created_at=datetime.now(timezone.utc),
    )
    session.add(bm)
    session.commit()
    session.refresh(bm)
    return {
        "id": bm.id,
        "book_id": bm.book_id,
        "sentence_index": bm.sentence_index,
        "page": bm.page,
        "label": bm.label,
        "created_at": bm.created_at.isoformat(),
    }


@router.get("/{book_id}")
def list_bookmarks(book_id: str, session: Session = Depends(get_session)):
    rows = session.exec(
        select(Bookmark)
        .where(Bookmark.book_id == book_id)
        .order_by(Bookmark.page, Bookmark.sentence_index)
    ).all()
    return [
        {
            "id": b.id,
            "book_id": b.book_id,
            "sentence_index": b.sentence_index,
            "page": b.page,
            "label": b.label,
            "created_at": b.created_at.isoformat(),
        }
        for b in rows
    ]


@router.delete("/{bookmark_id}")
def delete_bookmark(bookmark_id: int, session: Session = Depends(get_session)):
    bm = session.get(Bookmark, bookmark_id)
    if not bm:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    session.delete(bm)
    session.commit()
    return {"ok": True}
