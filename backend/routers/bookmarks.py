from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from db.database import get_session
from db.models import Bookmark, Sentence
from routers.deps import require_book

router = APIRouter(prefix="/bookmarks")


class BookmarkCreate(BaseModel):
    book_id: str
    sentence_index: int
    label: str


def _serialize(bookmark: Bookmark) -> dict:
    """The bookmark wire shape, in one place.

    Creating and listing a bookmark each built this dict by hand, identically
    apart from the variable name. The two must agree — a field added to one and
    not the other is a client-visible inconsistency — so the shape lives here,
    matching the `_serialize` helpers in routers/library.py and routers/folders.py.
    """
    return {
        "id": bookmark.id,
        "book_id": bookmark.book_id,
        "sentence_index": bookmark.sentence_index,
        "page": bookmark.page,
        "label": bookmark.label,
        "created_at": bookmark.created_at.isoformat(),
    }


@router.post("")
def create_bookmark(body: BookmarkCreate, session: Session = Depends(get_session)):
    # Only the existence check matters here; the row itself is not used, so the
    # return value is deliberately discarded.
    require_book(session, body.book_id)

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
    return _serialize(bm)


@router.get("/{book_id}")
def list_bookmarks(book_id: str, session: Session = Depends(get_session)):
    rows = session.exec(
        select(Bookmark)
        .where(Bookmark.book_id == book_id)
        .order_by(Bookmark.page, Bookmark.sentence_index)
    ).all()
    return [_serialize(row) for row in rows]


@router.delete("/{bookmark_id}")
def delete_bookmark(bookmark_id: int, session: Session = Depends(get_session)):
    bm = session.get(Bookmark, bookmark_id)
    if not bm:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    session.delete(bm)
    session.commit()
    return {"ok": True}
