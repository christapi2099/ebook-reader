"""Guards shared by more than one router.

The same "does this book exist?" prelude had been written out at eight call
sites across four routers, each raising the identical 404 with the identical
detail string. Two shapes had grown: some callers only wanted the check, others
went on to use the row. ``require_book`` returns the row, so it serves both —
the check-only callers simply ignore the return value — and the status code and
message now have exactly one home.

``routers/folders.py`` has the narrower ``_require`` for folders; that one stayed
where it is because it is used only there and extracting it would move a single
function without removing any duplication.
"""
from fastapi import HTTPException
from sqlmodel import Session

from db.models import Book


def require_book(session: Session, book_id: str) -> Book:
    """Return the book, or raise the 404 every caller wants when it is missing."""
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book
