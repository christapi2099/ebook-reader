"""Loading a book's sentences for synthesis.

Both synthesis paths need the same thing: every sentence of a book, in index
order, keyed by index, and detached from the ORM session so it outlives a
short-lived one. The streaming WebSocket keeps this data for the whole life of a
connection, and the MP3 export walks it for the whole export.

They had grown two copies of the same query, and the copies had already drifted —
one returned the full model dump, the other a hand-picked subset — so the query
and the shape live here now, in one place, with the fields the callers actually
read made explicit.
"""
from sqlmodel import Session, select

import db.database as _db
from db.models import Book, Sentence


def load_sentences(book_id: str) -> dict[int, dict] | None:
    """Return ``{index: sentence}`` for a book, or None when the book is unknown.

    Distinguishing "no such book" from "a book with no sentences" matters: the
    WebSocket closes the connection with 4004 for the former and streams an
    immediate ``complete`` for the latter, so the two cannot be collapsed.

    The session is deliberately short-lived. Callers hold the result far longer
    than a session should live, so every row is copied into a plain dict before
    the session closes rather than being returned attached and used lazily.

    Only the fields synthesis reads are copied, which keeps the payload small and
    turns "what does a caller depend on?" into something a reader can see.
    """
    with Session(_db.engine) as session:
        if session.get(Book, book_id) is None:
            return None
        rows = session.exec(
            select(Sentence)
            .where(Sentence.book_id == book_id)
            .order_by(Sentence.index)
        ).all()
        return {
            row.index: {
                "text": row.text,
                "filtered": row.filtered,
                "chapter": row.chapter,
                "chapter_title": row.chapter_title,
            }
            for row in rows
        }
