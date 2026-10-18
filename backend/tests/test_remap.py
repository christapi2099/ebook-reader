import json
import time
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

import db.database as _db
import storage
from db.models import Book, Progress, Sentence
from services import remap as rm


def S(i, text, page=0, box=None, chapter=0):
    x0, y0, x1, y1 = box or (0, 0, 0, 0)
    return dict(index=i, text=text, page=page, x0=x0, y0=y0, x1=x1, y1=y1, chapter=chapter)


def texts(ts, **kw):
    return [S(i, t, **kw) for i, t in enumerate(ts)]


def test_identical_text_keeps_indices():
    a = texts(["a", "b", "c"])
    assert rm.compute_remap(a, list(a)) == [0, 1, 2]


def test_duplicate_chapter_lines_plus_insertion():
    old = ["Chapter 1"] * 1200 + ["tail one", "tail two"]
    new = ["Chapter 1"] * 600 + ["NEW LINE"] + ["Chapter 1"] * 600 + ["tail one", "tail two"]
    m = rm.compute_remap(texts(old), texts(new))
    assert m[1200] == 1201 and m[1201] == 1202
    assert m[0] == 0 and m[599] == 599
    assert m[600] == 601 and m[1199] == 1200
    assert len(set(m)) == len(m)  # monotone, no collisions


def test_12000_under_one_second():
    old = [f"sentence number {i}" for i in range(12000)]
    new = old[:5000] + ["inserted"] + old[5000:9000] + old[9010:]
    t = time.perf_counter()
    m = rm.compute_remap(texts(old), texts(new))
    assert time.perf_counter() - t < 1.0
    assert m[4999] == 4999 and m[5000] == 5001
    assert m[9005] is None and m[9010] == 9001
    assert new[m[11999]] == old[11999]


def test_12000_geometry_under_one_second():
    old = [S(i, f"s{i}", page=i // 20, box=(0, i % 20 * 10, 5, i % 20 * 10 + 8)) for i in range(12000)]
    t = time.perf_counter()
    m = rm.compute_remap(old, [dict(o) for o in old])
    assert time.perf_counter() - t < 1.0
    assert m == list(range(12000))


def test_geometry_nearest_bbox_same_page():
    old = [S(0, "x", 1, (0, 0, 10, 10)), S(1, "y", 1, (0, 100, 10, 110)), S(2, "z", 2, (0, 0, 10, 10))]
    new = [S(0, "q", 1, (0, 98, 10, 108)), S(1, "w", 1, (0, 1, 10, 11)), S(2, "e", 3, (0, 0, 10, 10))]
    assert rm.compute_remap(old, new) == [1, 0, None]


def test_geometry_duplicate_text_disambiguated_by_position():
    old = [S(0, "Chapter 1", 0, (0, 0, 9, 9)), S(1, "Chapter 1", 0, (0, 50, 9, 59))]
    new = [S(0, "Chapter 1", 0, (0, 0, 9, 9)), S(1, "intro", 0, (0, 25, 9, 34)),
           S(2, "Chapter 1", 0, (0, 50, 9, 59))]
    assert rm.compute_remap(old, new) == [0, 2]


def test_chapter_bounded_windows():
    old = texts(["same", "a"], chapter=0) + texts(["same", "b"], chapter=1)
    new = texts(["same", "a"], chapter=0) + texts(["same", "b"], chapter=1)
    new = [dict(n, index=i) for i, n in enumerate(new)]
    assert rm.compute_remap(old, new) == [0, 1, 2, 3]
    # chapter missing in new -> None
    assert rm.compute_remap(old, new[:2]) == [0, 1, None, None]


def test_deleted_tail_is_none_and_translate_clamps():
    m = rm.compute_remap(texts(["a", "b", "c", "d"]), texts(["a", "b"]))
    assert m == [0, 1, None, None]
    assert rm.translate_index(m, 3, new_count=2) == 1
    assert rm.translate_index(m, 0, new_count=2) == 0
    assert rm.translate_index(m, 99, new_count=2) == 1
    assert rm.translate_index([], 5, new_count=0) == 0


def test_translate_none_in_middle_uses_previous_match():
    m = [0, None, 2]
    assert rm.translate_index(m, 1, new_count=3) == 0
    assert rm.translate_index([None, 1], 0, new_count=3) == 1  # next match if none before


# ---- chain + progress ----
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "RENDERS_DIR", tmp_path / "renders")
    old = _db.engine
    _db.create_engine_and_tables(f"sqlite:///{tmp_path/'t.db'}")
    yield tmp_path
    _db.engine.dispose()
    _db.engine = old


def write_remap(bid, gen, mapping):
    """File for transition gen-1 -> gen lives in dir {gen}, named remap_from_{gen-1}."""
    d = storage.RENDERS_DIR / bid / str(gen)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"remap_from_{gen-1}.json").write_text(
        json.dumps({str(k): v for k, v in mapping.items()}))


def mk_book(bid, gen, n):
    with Session(_db.engine) as s:
        s.add(Book(id=bid, title="t", file_path="x", file_type="epub", page_count=1,
                   created_at=datetime.now(UTC), ready_generation=gen))
        for i in range(n):
            s.add(Sentence(book_id=bid, index=i, text=f"t{i}", page=0, x0=0, y0=0, x1=0, y1=0))
        s.commit()


def test_chain_composes_and_matches_publish_format(env):
    write_remap("b", 2, {0: 1, 1: 2, 2: None})
    write_remap("b", 3, {0: 0, 1: 1, 2: 2, 3: 3})
    chain = rm.load_remap_chain("b", 1, 3)
    assert chain[0] == 1 and chain[1] == 2 and chain[2] is None
    assert rm.load_remap_chain("b", 3, 3) is None  # identity
    assert rm.load_remap_chain("b", 2, 3) == [0, 1, 2, 3]


def test_chain_missing_file_returns_none_marker(env):
    write_remap("b", 3, {0: 0})
    with pytest.raises(rm.RemapUnavailable):
        rm.load_remap_chain("b", 1, 3)


def test_chain_from_real_publish_generation(env, tmp_path):
    from services import render_store as rs
    mk_book("b", 1, 3)
    (storage.RENDERS_DIR / "b" / "1").mkdir(parents=True)
    tmp = storage.RENDERS_DIR / "b" / ".tmp-x"
    tmp.mkdir()
    new = [dict(index=i, text=f"n{i}", page=0, x0=0, y0=0, x1=0, y1=0) for i in range(4)]
    g = rs.publish_generation("b", tmp, new, {0: 1, 1: 2, 2: 3}, "fp")
    assert g == 2
    assert rm.load_remap_chain("b", 1, 2) == [1, 2, 3]


def progress(bid):
    with Session(_db.engine) as s:
        return s.get(Progress, bid)


def test_stale_progress_write_translated(env):
    mk_book("b", 3, 5)
    write_remap("b", 2, {0: 1, 1: 2, 2: 3})      # a line inserted at the front
    write_remap("b", 3, {0: 0, 1: 1, 2: 2, 3: 3, 4: 4})
    assert rm.translate_progress_write("b", 1, 1) == 2
    assert progress("b").sentence_index == 2


def test_current_generation_write_is_untouched(env):
    mk_book("b", 3, 5)
    assert rm.translate_progress_write("b", 3, 4) == 4
    assert progress("b").sentence_index == 4


def test_pagehide_beacon_during_swap_lands_on_same_text(env):
    """Old gen 1 text; the reader was on 'target'. Gen 2 inserts two lines before it."""
    old = texts(["a", "b", "target", "d"])
    new = texts(["a", "X", "Y", "b", "target", "d"])
    mapping = rm.compute_remap(old, new)
    mk_book("b", 2, len(new))
    write_remap("b", 2, dict(enumerate(mapping)))
    idx = rm.translate_progress_write("b", 1, 2)
    assert new[idx]["text"] == "target"


def test_stale_write_at_deleted_tail_clamps_without_deleting(env):
    mk_book("b", 2, 2)
    write_remap("b", 2, {0: 0, 1: 1, 2: None, 3: None})
    assert rm.translate_progress_write("b", 1, 3) == 1


def test_missing_chain_clamps_instead_of_rejecting(env):
    mk_book("b", 3, 4)
    assert rm.translate_progress_write("b", 1, 10) == 3


def test_future_generation_clamped_to_ready(env):
    mk_book("b", 2, 4)
    assert rm.translate_progress_write("b", 9, 2) == 2
