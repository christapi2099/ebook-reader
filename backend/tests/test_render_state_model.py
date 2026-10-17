"""Phase 1b: Book render-state columns, their migration, and render_state helpers."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.database as _db  # noqa: E402
from db.models import Book  # noqa: E402
from services import render_state  # noqa: E402

NEW_COLS = {
    "ready_generation", "ready_fingerprint", "job_status", "target_fingerprint",
    "phase", "progress", "error_message", "render_priority", "lease_owner",
    "lease_expires", "voice",
}


def _cols(engine):
    with engine.connect() as c:
        return {r[1]: r for r in c.execute(text("PRAGMA table_info(book)"))}


def test_fresh_db_has_columns(tmp_path):
    eng = _db.create_engine_and_tables(f"sqlite:///{tmp_path/'f.db'}")
    assert NEW_COLS <= set(_cols(eng))


def _legacy_db(path):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE book (id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT,"
        " file_path TEXT NOT NULL, file_type TEXT NOT NULL, page_count INTEGER NOT NULL,"
        " cover_page INTEGER DEFAULT 0, created_at DATETIME NOT NULL, last_opened DATETIME,"
        " ephemeral BOOLEAN DEFAULT 0)"
    )
    con.execute("INSERT INTO book (id,title,file_path,file_type,page_count,created_at)"
                " VALUES ('b1','T','/x','pdf',3,'2026-01-01 00:00:00')")
    con.commit()
    con.close()


def test_migration_flips_existing_books_to_pending(tmp_path):
    p = tmp_path / "old.db"
    _legacy_db(p)
    eng = _db.create_engine_and_tables(f"sqlite:///{p}")
    assert NEW_COLS <= set(_cols(eng))
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT job_status, ready_generation, render_priority FROM book WHERE id='b1'"
        )).one()
    assert row[0] == "pending" and row[1] is None and row[2] == 0


def test_migration_idempotent_and_does_not_reset_state(tmp_path):
    p = tmp_path / "old.db"
    _legacy_db(p)
    eng = _db.create_engine_and_tables(f"sqlite:///{p}")
    with eng.begin() as c:
        c.execute(text("UPDATE book SET job_status='idle', ready_generation=2"))
    eng = _db.create_engine_and_tables(f"sqlite:///{p}")  # second run
    with eng.connect() as c:
        row = c.execute(text("SELECT job_status, ready_generation FROM book")).one()
    assert tuple(row) == ("idle", 2)


def test_new_book_defaults_idle(tmp_path):
    from sqlmodel import Session
    eng = _db.create_engine_and_tables(f"sqlite:///{tmp_path/'n.db'}")
    with Session(eng) as s:
        s.add(Book(id="x", title="t", file_path="/f", file_type="pdf",
                   page_count=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        b = s.get(Book, "x")
        assert b.job_status == "idle" and b.ready_generation is None
        assert b.render_priority == 0 and b.progress == 0.0


def test_is_readable():
    b = Book(id="x", title="t", file_path="/f", file_type="pdf", page_count=1,
             created_at=datetime.now(timezone.utc))
    assert not render_state.is_readable(b)
    b.ready_generation = 0
    assert render_state.is_readable(b)
    assert render_state.JOB_STATUSES == ("idle", "pending", "processing", "error", "cancelled")
