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


# --------------------------------------------------------------------------- #
# _migrate() contract, column by column
# --------------------------------------------------------------------------- #
# create_all() creates missing *tables* but never adds a column to a table that
# already exists, so every column below is _migrate()'s responsibility. This
# mapping is the regression net for the whole set: one migration was silently
# lost to a concurrent edit once, and a per-column expectation is what catches
# that instead of a hand-picked subset.

MIGRATED_COLUMNS = {
    "audiocache": {"word_timestamps"},
    "sentence": {"words", "chapter", "chapter_title"},
    "usersettings": {"highlight_enabled", "tts_engine"},
    "mp3export": {
        "effective_speed",
        "phase",
        "batches_done",
        "batches_total",
        "format",
        "bitrate_kbps",
        "options",
    },
    "book": {"folder_id"},
}

#: Indexes _migrate() owns. Both are created with IF NOT EXISTS so they are
#: idempotent, but they must actually exist: the audio-cache sweep orders by
#: ``created_at`` on a table holding ~800 MB of PCM, and every Book query filters
#: on ``folder_id``.
MIGRATED_INDEXES = {
    "audiocache": {"ix_audiocache_created_at"},
    "book": {"ix_book_folder_id"},
}

#: The shape of a database written before any of the above existed.
_LEGACY_TABLES = (
    'CREATE TABLE book (id TEXT PRIMARY KEY, title TEXT, author TEXT, file_path TEXT, '
    "file_type TEXT, page_count INTEGER, cover_page INTEGER DEFAULT 0, "
    "created_at TIMESTAMP, last_opened TIMESTAMP, ephemeral BOOLEAN DEFAULT 0)",
    'CREATE TABLE sentence (id INTEGER PRIMARY KEY, book_id TEXT, "index" INTEGER, '
    "text TEXT, page INTEGER, x0 FLOAT, y0 FLOAT, x1 FLOAT, y1 FLOAT, filtered BOOLEAN)",
    "CREATE TABLE audiocache (text_hash TEXT PRIMARY KEY, audio_data BLOB, "
    "duration_ms INTEGER, voice TEXT, created_at TIMESTAMP)",
    "CREATE TABLE usersettings (id INTEGER PRIMARY KEY, last_book_id TEXT, "
    "last_sentence_index INTEGER DEFAULT 0)",
    "CREATE TABLE mp3export (id INTEGER PRIMARY KEY, book_id TEXT, voice TEXT, speed REAL, "
    "status TEXT, progress INTEGER DEFAULT 0, file_path TEXT, file_size INTEGER, "
    "error_message TEXT, created_at TIMESTAMP)",
)


@pytest.fixture
def legacy_engine(tmp_path):
    """A pre-migration database carrying one row per table, already migrated."""
    db_file = tmp_path / "legacy-full.db"
    conn = sqlite3.connect(db_file)
    for statement in _LEGACY_TABLES:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO book (id, title, file_path, file_type, page_count, created_at) "
        "VALUES ('legacy-book', 'Legacy', '/tmp/l.pdf', 'pdf', 1, '2024-01-01 00:00:00')"
    )
    conn.execute(
        'INSERT INTO sentence (book_id, "index", text, page, x0, y0, x1, y1, filtered) '
        "VALUES ('legacy-book', 0, 'Legacy sentence.', 0, 0, 0, 1, 1, 0)"
    )
    conn.execute(
        "INSERT INTO audiocache (text_hash, audio_data, duration_ms, voice, created_at) "
        "VALUES ('legacy-hash', X'00', 100, 'af_heart', '2024-01-01 00:00:00')"
    )
    conn.execute("INSERT INTO usersettings (id, last_book_id, last_sentence_index) "
                 "VALUES (1, 'legacy-book', 7)")
    conn.execute(
        "INSERT INTO mp3export (id, book_id, voice, speed, status, created_at) "
        "VALUES (1, 'legacy-book', 'af_heart', 1.0, 'done', '2024-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    return _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")


@pytest.mark.parametrize("table", sorted(MIGRATED_COLUMNS))
def test_migrate_adds_every_column_it_owns(legacy_engine, table):
    columns = {c["name"] for c in inspect(legacy_engine).get_columns(table)}
    missing = MIGRATED_COLUMNS[table] - columns
    assert not missing, f"_migrate() no longer adds {sorted(missing)} to {table}"


@pytest.mark.parametrize("table", sorted(MIGRATED_INDEXES))
def test_migrate_creates_the_indexes_it_owns(legacy_engine, table):
    with legacy_engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text(f"PRAGMA index_list({table})"))}
    missing = MIGRATED_INDEXES[table] - existing
    assert not missing, f"missing index(es) on {table}: {sorted(missing)}"


def test_legacy_rows_survive_and_get_the_declared_defaults(legacy_engine):
    """Adding a column must not lose the rows that predate it, and the defaults
    have to say something true about them: every pre-existing export is an MP3,
    and existing installs had highlighting on."""
    with legacy_engine.connect() as conn:
        book = conn.execute(
            text("SELECT title, folder_id FROM book WHERE id = 'legacy-book'")
        ).one()
        sentence = conn.execute(
            text('SELECT text, words, chapter, chapter_title FROM sentence '
                 'WHERE book_id = \'legacy-book\'')
        ).one()
        settings = conn.execute(
            text("SELECT last_sentence_index, highlight_enabled, tts_engine "
                 "FROM usersettings WHERE id = 1")
        ).one()
        export = conn.execute(
            text("SELECT status, format, batches_done, batches_total, bitrate_kbps, options "
                 "FROM mp3export WHERE id = 1")
        ).one()

    assert book.title == "Legacy"
    assert book.folder_id is None, "a book that predates folders belongs to no folder"

    assert sentence.text == "Legacy sentence."
    assert sentence.words is None, "word timestamps were not stored back then"
    assert sentence.chapter == 0
    assert sentence.chapter_title is None

    assert settings.last_sentence_index == 7
    assert settings.highlight_enabled == 1
    assert settings.tts_engine is None, "NULL means 'never chosen', so KOKORO_BACKEND decides"

    assert export.status == "done"
    assert export.format == "mp3"
    assert export.batches_done == 0
    assert export.batches_total == 0
    assert export.bitrate_kbps is None
    assert export.options is None


def test_migrate_is_idempotent_on_the_full_legacy_schema(legacy_engine):
    """Re-running every migration must change nothing: no duplicated columns, no
    rewritten indexes, no data touched."""
    first = _schema_snapshot(legacy_engine)
    _db._migrate(legacy_engine)
    _db._migrate(legacy_engine)
    assert _schema_snapshot(legacy_engine) == first
    for table, expected in MIGRATED_COLUMNS.items():
        assert expected <= set(first[table])


def test_fresh_databases_have_the_same_columns_as_migrated_ones(tmp_path):
    """A new install and an upgraded install must not diverge.

    This is the failure mode that hurts most in production: the app works on a
    fresh database and dies on the developer's 800 MB one (or the reverse), which
    is exactly what happened when ``book.folder_id`` was only in the model.
    """
    migrated = _schema_snapshot(
        _db.create_engine_and_tables(db_url=f"sqlite:///{tmp_path / 'migrated.db'}")
    )
    from sqlmodel import SQLModel

    fresh_engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    SQLModel.metadata.create_all(fresh_engine)
    fresh = _schema_snapshot(fresh_engine)
    _db._migrate(fresh_engine)

    assert _schema_snapshot(fresh_engine) == fresh, "_migrate() altered a fresh database"
    assert migrated == fresh

