"""Regression tests for schema creation in db/database.py.

`create_engine_and_tables()` called `SQLModel.metadata.create_all()` without ever
importing `db.models`. Table metadata is only registered as a side effect of
importing the model module, so any caller that had not (transitively) imported
`db.models` got a database with no tables, and `_migrate()` then died on
`PRAGMA table_info(...)` + `ALTER TABLE audiocache ...` with:

    sqlite3.OperationalError: no such table: audiocache

That is a startup crash. The subprocess test below is the honest reproduction: a
fresh interpreter with no other module having imported the models.
"""
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlmodel import create_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import db.database as _db  # noqa: E402
from db.models import (  # noqa: E402
    AudioCache,
    Book,
    Bookmark,
    Folder,
    MP3Export,
    Progress,
    Sentence,
    UserSettings,
)

EXPECTED_TABLES = {"audiocache", "book", "bookmark", "folder", "mp3export", "progress", "sentence", "usersettings"}


def test_every_model_is_registered_on_the_shared_metadata():
    registered = set(_db.SQLModel.metadata.tables)
    assert registered == EXPECTED_TABLES
    # Guard against a model being added to db.models without a table=True mapping.
    for model in (Book, Sentence, AudioCache, Progress, Bookmark, MP3Export, UserSettings, Folder):
        assert model.__tablename__ in registered


def test_create_engine_and_tables_creates_every_table(tmp_path):
    engine = _db.create_engine_and_tables(db_url=f"sqlite:///{tmp_path / 'fresh.db'}")
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_fresh_interpreter_without_db_models_import_creates_tables():
    """The original defect: no caller had imported db.models yet."""
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(BACKEND_DIR)!r})
        from sqlalchemy import inspect
        import db.database as d

        assert "db.models" not in sys.modules, "test precondition: models not imported"
        engine = d.create_engine_and_tables(db_url="sqlite:///:memory:")
        print(",".join(sorted(inspect(engine).get_table_names())))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stdout}\n{proc.stderr}"
    assert "no such table" not in proc.stderr
    assert set(proc.stdout.strip().split(",")) == EXPECTED_TABLES


def test_calling_create_engine_and_tables_twice_is_idempotent(tmp_path):
    db_file = tmp_path / "twice.db"
    first = _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
    before = _schema_snapshot(first)

    second = _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
    after = _schema_snapshot(second)

    assert before == after
    assert set(after) == EXPECTED_TABLES


def test_second_startup_does_not_rewrite_the_database_file(tmp_path):
    """_migrate() must stay O(1) — no table rewrite, no full scan on every boot."""
    db_file = tmp_path / "mtime.db"
    _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
    stat_before = (db_file.stat().st_size, db_file.stat().st_mtime_ns)

    _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
    stat_after = (db_file.stat().st_size, db_file.stat().st_mtime_ns)

    assert stat_after == stat_before, "second startup wrote to the database file"


def test_legacy_database_gains_the_new_columns(tmp_path):
    """Existing ebooks_reader.db files must keep working (the _migrate contract)."""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        'CREATE TABLE sentence (id INTEGER PRIMARY KEY, book_id TEXT, "index" INTEGER, '
        "text TEXT, page INTEGER, x0 FLOAT, y0 FLOAT, x1 FLOAT, y1 FLOAT, filtered BOOLEAN)"
    )
    conn.execute("CREATE TABLE audiocache (text_hash TEXT PRIMARY KEY, audio_data BLOB, duration_ms INTEGER, voice TEXT, created_at TIMESTAMP)")
    conn.execute("CREATE TABLE usersettings (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    engine = _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
    columns = {c["name"] for c in inspect(engine).get_columns("sentence")}
    assert {"words", "chapter", "chapter_title"} <= columns
    assert "word_timestamps" in {c["name"] for c in inspect(engine).get_columns("audiocache")}
    assert "highlight_enabled" in {c["name"] for c in inspect(engine).get_columns("usersettings")}
    # And the tables that did not exist yet were created.
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_migrate_is_idempotent_on_a_legacy_database(tmp_path):
    db_file = tmp_path / "legacy2.db"
    conn = sqlite3.connect(db_file)
    conn.execute('CREATE TABLE sentence (id INTEGER PRIMARY KEY, book_id TEXT, "index" INTEGER, text TEXT, page INTEGER, x0 FLOAT, y0 FLOAT, x1 FLOAT, y1 FLOAT, filtered BOOLEAN)')
    conn.commit()
    conn.close()

    engine = _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
    first = _schema_snapshot(engine)
    _db._migrate(engine)
    _db._migrate(engine)
    assert _schema_snapshot(engine) == first
    assert "chapter" in first["sentence"]


def _schema_snapshot(engine) -> dict[str, list[str]]:
    """Column names per table, ordered — cheap and comparable."""
    with engine.connect() as conn:
        tables = sorted(inspect(engine).get_table_names())
        return {
            table: [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
            for table in tables
        }
