"""Phase 1b: claim/lease, triggers, supersede/cancel/delete (services/render_jobs.py)."""
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.database as _db  # noqa: E402
from db.models import Book, Sentence  # noqa: E402
from services import render_jobs as rj  # noqa: E402

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def eng(db_engine, monkeypatch):
    monkeypatch.setattr(_db, "engine", db_engine)
    return db_engine


def mk(eng, bid, **kw):
    kw.setdefault("created_at", T0)
    with Session(eng) as s:
        s.add(Book(id=bid, title=bid, file_path="x", file_type="pdf", page_count=1, **kw))
        s.commit()


def row(eng, bid):
    with Session(eng) as s:
        return s.get(Book, bid)


def test_two_workers_claim_once(eng):
    mk(eng, "a", job_status="pending", target_fingerprint="f1")
    got = []
    def w(n):
        got.append(rj.claim_next(n, T0))
    ts = [threading.Thread(target=w, args=(f"w{i}",)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert got.count("a") == 1 and got.count(None) == 3
    r = row(eng, "a")
    assert r.job_status == "processing" and r.lease_owner in {"w0", "w1", "w2", "w3"}


def test_live_lease_not_stolen_expired_reclaimed(eng):
    mk(eng, "a", job_status="pending")
    assert rj.claim_next("w1", T0, lease_s=120) == "a"
    assert rj.claim_next("w2", T0 + timedelta(seconds=60)) is None
    assert row(eng, "a").lease_owner == "w1"
    assert rj.claim_next("w2", T0 + timedelta(seconds=121)) == "a"
    assert row(eng, "a").lease_owner == "w2"


def test_renew_release_progress(eng):
    mk(eng, "a", job_status="pending")
    rj.claim_next("w1", T0, lease_s=10)
    assert rj.renew_lease("a", "w1", T0 + timedelta(seconds=5), lease_s=100)
    assert not rj.renew_lease("a", "other", T0, lease_s=100)
    assert row(eng, "a").lease_expires == T0 + timedelta(seconds=105)
    rj.mark_progress("a", 0.5, "synth")
    r = row(eng, "a"); assert (r.progress, r.phase) == (0.5, "synth")
    rj.release("a", "w1")
    r = row(eng, "a"); assert r.lease_owner is None and r.lease_expires is None
    assert r.job_status == "processing"  # release drops the lease only; a lease-less row is reclaimable


def test_priority_then_created(eng):
    mk(eng, "old", job_status="pending", created_at=T0)
    mk(eng, "new", job_status="pending", created_at=T0 + timedelta(hours=1))
    mk(eng, "hi", job_status="pending", created_at=T0 + timedelta(hours=2), render_priority=5)
    order = [rj.claim_next("w", T0) for _ in range(3)]
    assert order == ["hi", "old", "new"]


def test_bump_priority_moves_to_front(eng):
    mk(eng, "old", job_status="pending")
    mk(eng, "new", job_status="pending", created_at=T0 + timedelta(hours=1))
    rj.bump_priority("new")
    assert rj.claim_next("w", T0) == "new"


def test_debounce_absorbs_three_toggles(eng):
    mk(eng, "a", ready_generation=1, ready_fingerprint="R")
    rj.request_render("a", "X", now=T0)
    rj.request_render("a", "Y", now=T0 + timedelta(seconds=10))
    rj.request_render("a", "Z", now=T0 + timedelta(seconds=20))
    assert rj.claim_next("w", T0 + timedelta(seconds=50)) is None
    assert rj.claim_next("w", T0 + timedelta(seconds=81)) == "a"
    assert row(eng, "a").target_fingerprint == "Z"
    assert rj.claim_next("w2", T0 + timedelta(seconds=82)) is None


def test_immediate_request_skips_debounce(eng):
    mk(eng, "a")
    rj.request_render("a", "X", now=T0, debounce=False)
    assert rj.claim_next("w", T0) == "a"


def test_flip_back_cancels(eng):
    mk(eng, "a", ready_generation=1, ready_fingerprint="R")
    rj.request_render("a", "X", now=T0)
    assert row(eng, "a").job_status == "pending"
    rj.request_render("a", "R", now=T0)
    r = row(eng, "a")
    assert r.job_status == "cancelled" and r.ready_generation == 1
    assert rj.claim_next("w", T0 + timedelta(hours=1)) is None


def test_cancel(eng):
    mk(eng, "a", job_status="pending", ready_generation=2)
    rj.cancel("a")
    r = row(eng, "a"); assert r.job_status == "cancelled" and r.ready_generation == 2
    assert rj.check_job("a", None) == rj.CANCELLED


def test_supersede_midjob_single_final_target(eng):
    mk(eng, "a", ready_generation=1, ready_fingerprint="R")
    rj.request_render("a", "X", now=T0, debounce=False)
    assert rj.claim_next("w1", T0) == "a"
    assert rj.check_job("a", "X") == rj.CONTINUE
    rj.request_render("a", "Y", now=T0, debounce=False)   # while processing
    assert rj.check_job("a", "X") == rj.SUPERSEDED
    assert row(eng, "a").target_fingerprint == "Y"
    rj.requeue("a", "w1")
    assert rj.claim_next("w2", T0) == "a"
    assert rj.check_job("a", "Y") == rj.CONTINUE
    assert row(eng, "a").target_fingerprint == "Y"


def test_check_job_gone(eng):
    assert rj.check_job("nope", "X") == rj.GONE


def test_delete_during_render_cleans_up(eng, client, monkeypatch, tmp_path):
    import routers.library as lib
    monkeypatch.setattr(lib, "RENDERS_DIR", tmp_path / "renders")
    mk(eng, "a", job_status="pending")
    with Session(eng) as s:
        s.add(Sentence(book_id="a", index=0, text="hi", page=0, x0=0, y0=0, x1=1, y1=1)); s.commit()
    assert rj.claim_next("w", T0) == "a"
    d = tmp_path / "renders" / "a" / ".tmp-1"
    d.mkdir(parents=True); (d / "f").write_bytes(b"x")
    assert client.delete("/library/a").status_code == 200
    assert not (tmp_path / "renders" / "a").exists()
    assert rj.check_job("a", "X") == rj.GONE
    assert rj.mark_progress("a", 0.9, "p") is False   # no resurrection, no error
    assert rj.claim_next("w", T0) is None
    with Session(eng) as s:
        assert s.exec(select(Sentence)).all() == [] and s.exec(select(Book)).all() == []


def test_startup_expires_leases_left_by_a_dead_worker(db_engine, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session
    import db.database as _db
    from db.models import Book
    from services import render_jobs as rj

    monkeypatch.setattr(_db, "engine", db_engine)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(db_engine) as s:
        s.add(Book(id="orphan", title="o", file_path="x", file_type="text", page_count=1,
                   created_at=t0, job_status="processing", lease_owner="gone",
                   lease_expires=t0 + timedelta(hours=1)))
        s.commit()
    assert rj.expire_orphan_leases(now=t0) == 1
    assert rj.claim_next("new-worker", t0 + timedelta(days=1), debounce_s=0) == "orphan"


def test_processing_without_a_live_lease_is_reported_as_queued(db_engine, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from db.models import Book
    from services import render_jobs as rj

    now = datetime.now(timezone.utc)
    live = Book(id="l", title="l", file_path="x", file_type="text", page_count=1,
                created_at=now, job_status="processing", lease_expires=now + timedelta(minutes=5))
    dead = Book(id="d", title="d", file_path="x", file_type="text", page_count=1,
                created_at=now, job_status="processing", lease_expires=now - timedelta(minutes=5))
    assert rj.is_live(live) is True
    assert rj.is_live(dead) is False
    assert rj.is_live(Book(id="n", title="n", file_path="x", file_type="text", page_count=1,
                           created_at=now, job_status="processing", lease_expires=None)) is False
