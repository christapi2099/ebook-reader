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


def test_cover_is_the_first_page_of_a_pdf(client, tmp_path, monkeypatch):
    import fitz
    import storage
    from services import cover as cv

    monkeypatch.setattr(storage, "COVERS_DIR", tmp_path / "covers")

    pdf = tmp_path / "c.pdf"
    doc = fitz.open()
    doc.new_page(width=300, height=400)
    doc.save(str(pdf))
    doc.close()
    with Session(_db.engine) as s:
        s.add(Book(id="pdfcover", title="c", file_path=str(pdf), file_type="pdf", page_count=1,
                   created_at=T0))
        s.commit()
    r = client.get("/library/pdfcover/cover")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert cv.cover_path("pdfcover").is_file()


def test_no_cover_for_text_books(client):
    _book("txt", "ready")
    assert client.get("/library/txt/cover").status_code == 404


def _queued(bid, created_offset):
    with Session(_db.engine) as s:
        s.add(Book(id=bid, title=bid, file_path="x", file_type="text", page_count=1,
                   created_at=T0.replace(day=1 + created_offset), job_status="pending",
                   render_priority=0))
        s.commit()


def _queue_order():
    from sqlmodel import select
    from services import render_jobs as rj
    with Session(_db.engine) as s:
        rows = s.exec(select(Book).where(Book.job_status == "pending")).all()
    return [b.id for b in sorted(rows, key=lambda b: (-b.render_priority, b.created_at))]


def test_move_up_and_down_swap_with_the_neighbour(client):
    for i, bid in enumerate(["a", "b", "c"]):
        _queued(bid, i)
    assert _queue_order() == ["a", "b", "c"]
    assert client.post("/library/c/queue?direction=up").status_code == 200
    assert _queue_order() == ["a", "c", "b"]
    assert client.post("/library/a/queue?direction=down").status_code == 200
    assert _queue_order() == ["c", "a", "b"]


def test_move_at_the_edges_is_a_no_op(client):
    for i, bid in enumerate(["a", "b"]):
        _queued(bid, i)
    client.post("/library/a/queue?direction=up")
    assert _queue_order() == ["a", "b"]
    client.post("/library/b/queue?direction=down")
    assert _queue_order() == ["a", "b"]


def test_queue_move_refuses_bad_direction_and_non_queued_books(client):
    _queued("q", 0)
    _book("done", "ready")
    assert client.post("/library/q/queue?direction=sideways").status_code == 400
    assert client.post("/library/done/queue?direction=up").status_code == 409
