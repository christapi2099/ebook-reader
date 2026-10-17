"""Phase 0 regressions: SQLite hardening, delete_book orphans, RENDERS_DIR, .gitignore."""
import importlib
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def real_engine(tmp_path, monkeypatch):
    import db.database as database

    monkeypatch.setattr(database, "engine", None, raising=False)
    eng = database.create_engine_and_tables(f"sqlite:///{tmp_path / 'real.db'}")
    yield eng
    eng.dispose()


def test_engine_uses_wal_and_foreign_keys(real_engine):
    with real_engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL


def test_every_new_connection_gets_pragmas(real_engine):
    for _ in range(2):
        with real_engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_delete_book_leaves_no_orphans_and_removes_file(real_engine, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    import db.database as database
    from db.models import Book, Bookmark, MP3Export, Progress, Sentence, UserSettings
    from routers.library import delete_book

    monkeypatch.setattr(database, "engine", real_engine)
    f = tmp_path / "b.pdf"
    f.write_bytes(b"x")
    now = datetime.now(timezone.utc)
    with Session(real_engine) as s:
        s.add(Book(id="b1", title="t", file_path=str(f), file_type="pdf",
                   page_count=1, created_at=now))
        s.commit()
        s.add(Sentence(book_id="b1", index=0, text="a", page=0, x0=0, y0=0, x1=1, y1=1))
        s.add(Progress(book_id="b1", sentence_index=1, updated_at=now))
        s.add(Bookmark(book_id="b1", sentence_index=0, page=0, label="l", created_at=now))
        s.add(MP3Export(book_id="b1", voice="v", speed=1.0, status="done", created_at=now))
        s.add(UserSettings(id=1, last_book_id="b1"))
        s.commit()
        assert delete_book("b1", s) == {"ok": True}
        for model in (Sentence, Progress, Bookmark, MP3Export):
            assert s.exec(select(model)).all() == []
        assert s.get(Book, "b1") is None
        assert s.get(UserSettings, 1).last_book_id is None
    assert not f.exists()


def test_renders_dir_default_and_env_override(monkeypatch, tmp_path):
    import storage

    monkeypatch.delenv("RENDERS_DIR", raising=False)
    importlib.reload(storage)
    assert storage.RENDERS_DIR == Path(storage.__file__).parent / "renders"
    monkeypatch.setenv("RENDERS_DIR", str(tmp_path / "r"))
    importlib.reload(storage)
    assert storage.RENDERS_DIR == tmp_path / "r"
    monkeypatch.delenv("RENDERS_DIR")
    importlib.reload(storage)


def test_gitignore_contains_renders():
    lines = (REPO / ".gitignore").read_text().splitlines()
    assert "renders/" in [l.strip() for l in lines]


def test_compose_files_mount_renders_volume():
    for name in ("docker-compose.yml", "docker-compose.dev.yml"):
        text_ = (REPO / name).read_text()
        assert "backend_renders:/app/renders" in text_, name
        assert text_.count("backend_renders") >= 2, name


def test_agent_safety_excludes_renders():
    assert "--exclude=renders" in (REPO / "scripts/agent-safety.sh").read_text()
