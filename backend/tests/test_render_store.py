import json
import os
import threading
from collections import namedtuple
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

import db.database as _db
import storage
from db.models import Book, Progress, Sentence
from services import render_store as rs


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "RENDERS_DIR", tmp_path / "renders")
    (tmp_path / "renders").mkdir()
    old = _db.engine
    _db.create_engine_and_tables(f"sqlite:///{tmp_path/'t.db'}")
    yield tmp_path
    _db.engine.dispose()
    _db.engine = old


def mk_book(bid="b1", gen=None):
    with Session(_db.engine) as s:
        s.add(Book(id=bid, title="t", file_path="x", file_type="pdf", page_count=1,
                   created_at=datetime.now(UTC), ready_generation=gen,
                   job_status="processing"))
        s.commit()


def sent(i, text=None):
    return dict(index=i, text=text or f"s{i}", page=0, x0=0, y0=0, x1=1, y1=1)


def seed_gen(bid, gen, tiers=("1.0",), manifest=True):
    d = storage.RENDERS_DIR / bid / str(gen)
    d.mkdir(parents=True)
    for t in tiers:
        (d / f"audio_{t}.opus").write_bytes(b"opus" + t.encode())
    if manifest:
        (d / "manifest.json").write_text(json.dumps({"tiers": {t: {} for t in tiers}}))
    return d


def snapshot(bid, gen):
    d = storage.RENDERS_DIR / bid / str(gen)
    files = {p.name: p.read_bytes() for p in d.iterdir()}
    with Session(_db.engine) as s:
        rows = [(r.index, r.text) for r in s.exec(
            select(Sentence).where(Sentence.book_id == bid).order_by(Sentence.index))]
    return files, rows


def setup_old(bid="b1"):
    mk_book(bid, gen=1)
    seed_gen(bid, 1)
    with Session(_db.engine) as s:
        s.add_all(Sentence(book_id=bid, **sent(i, f"old{i}")) for i in range(3))
        s.commit()


def new_tmp(bid="b1", name=".tmp-abc"):
    d = storage.RENDERS_DIR / bid / name
    d.mkdir(parents=True)
    (d / "audio_1.0.opus").write_bytes(b"newaudio")
    (d / "manifest.json").write_text(json.dumps({"tiers": {"1.0": {}}}))
    return d


def test_publish_success(env):
    setup_old()
    tmp = new_tmp()
    gen = rs.publish_generation("b1", tmp, [sent(i) for i in range(4)], {0: 0}, "fp2")
    assert gen == 2
    assert (storage.RENDERS_DIR / "b1/2/remap_from_1.json").exists()
    assert (storage.RENDERS_DIR / "b1/1").exists()  # never deleted inline
    with Session(_db.engine) as s:
        b = s.get(Book, "b1")
        assert (b.ready_generation, b.ready_fingerprint, b.job_status, b.phase, b.progress) == (2, "fp2", "idle", None, 1.0)
        assert len(s.exec(select(Sentence)).all()) == 4


def test_fail_mid_synthesis(env):
    setup_old()
    before = snapshot("b1", 1)
    new_tmp()
    with pytest.raises(RuntimeError):
        raise RuntimeError("synth died")  # publish never reached
    assert snapshot("b1", 1) == before
    assert rs.reconcile() == []


def test_fail_after_encode(env):
    setup_old()
    before = snapshot("b1", 1)
    tmp = new_tmp()
    with pytest.raises(RuntimeError):
        rs.publish_generation("b1", tmp, [sent(0)], {}, "fp", fail_at="after_encode")
    assert tmp.exists() and not (storage.RENDERS_DIR / "b1/2").exists()
    assert snapshot("b1", 1) == before


def test_fail_after_rename_callback(env):
    setup_old()
    before = snapshot("b1", 1)
    tmp = new_tmp()
    seen = []

    def hook(p):
        seen.append(p)
        if p == "after_rename":
            raise RuntimeError("crash")
    with pytest.raises(RuntimeError):
        rs.publish_generation("b1", tmp, [sent(0)], {}, "fp", fail_at=hook)
    assert seen == ["after_encode", "after_rename"]
    assert snapshot("b1", 1) == before
    assert (storage.RENDERS_DIR / "b1/2").exists()  # orphan
    with Session(_db.engine) as s:
        b = s.get(Book, "b1")
        assert b.ready_generation == 1
    assert rs.reconcile() == []
    rs.gc()
    assert not (storage.RENDERS_DIR / "b1/2").exists()
    assert snapshot("b1", 1) == before


def test_reconcile_missing(env):
    mk_book("a", 1); seed_gen("a", 1, manifest=False)
    mk_book("b", 1); d = seed_gen("b", 1, tiers=("1.0", "2.0")); (d / "audio_2.0.opus").unlink()
    mk_book("c", 1); seed_gen("c", 1, tiers=("1.0", "2.0"))
    assert sorted(rs.reconcile()) == ["a", "b"]
    with Session(_db.engine) as s:
        for k in "ab":
            b = s.get(Book, k)
            assert b.ready_generation is None and b.job_status == "pending"
            assert b.error_message == "render artifacts missing"
        assert s.get(Book, "c").ready_generation == 1


def test_gc(env):
    mk_book("b1", 2)
    seed_gen("b1", 1); seed_gen("b1", 2); seed_gen("b1", 3)
    old = storage.RENDERS_DIR / "b1/.tmp-old"; old.mkdir()
    new = storage.RENDERS_DIR / "b1/.tmp-new"; new.mkdir()
    os.utime(old, (1, 1))
    seed_gen("ghost", 1)
    rs.gc()
    r = storage.RENDERS_DIR
    assert not (r / "b1/1").exists() and not (r / "b1/3").exists()
    assert (r / "b1/2").exists() and new.exists()
    assert not old.exists() and not (r / "ghost").exists()


def test_has_room(env):
    U = namedtuple("U", "total used free")
    assert rs.has_room(100, disk_usage=lambda p: U(0, 0, 301))
    assert not rs.has_room(100, disk_usage=lambda p: U(0, 0, 300))


def test_big_swap_with_concurrent_progress(env):
    setup_old()
    with Session(_db.engine) as s:
        s.add(Progress(book_id="b1", sentence_index=0, updated_at=datetime.now(UTC)))
        s.commit()
    tmp = new_tmp()
    errors, stop = [], threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            try:
                with Session(_db.engine) as s:
                    p = s.get(Progress, "b1")
                    p.sentence_index = i
                    s.add(p)
                    s.commit()
                i += 1
            except Exception as e:
                errors.append(e)
                return
    ts = [threading.Thread(target=writer) for _ in range(3)]
    [t.start() for t in ts]
    try:
        rs.publish_generation("b1", tmp, [sent(i) for i in range(5000)], {}, "fp")
    finally:
        stop.set()
        [t.join() for t in ts]
    assert not errors, errors
    with Session(_db.engine) as s:
        assert len(s.exec(select(Sentence)).all()) == 5000
