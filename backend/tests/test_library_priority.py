"""Move-to-front for queued renders: the API bumps priority, only for queued books."""
from datetime import datetime, timezone

from sqlmodel import Session

import db.database as _db
from db.models import Book

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _book(bid, status):
    with Session(_db.engine) as s:
        s.add(Book(id=bid, title=bid, file_path="x", file_type="text", page_count=1,
                   created_at=T0, job_status=status))
        s.commit()


def test_move_to_front_bumps_a_queued_book_above_the_others(client):
    _book("q1", "pending")
    _book("q2", "pending")
    r = client.post("/library/q2/priority")
    assert r.status_code == 200
    with Session(_db.engine) as s:
        assert s.get(Book, "q2").render_priority > s.get(Book, "q1").render_priority


def test_move_to_front_refuses_a_book_that_is_not_queued(client):
    _book("r1", "ready")
    assert client.post("/library/r1/priority").status_code == 409
