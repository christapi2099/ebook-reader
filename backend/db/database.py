import os
from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text

# DB_PATH env var allows Docker volume override; falls back to local file
_DEFAULT_DB = os.environ.get("DB_PATH") or str(Path(__file__).parent.parent / "ebook_reader.db")

engine = None


def create_engine_and_tables(db_url: str | None = None) -> object:
    global engine
    # Imported for the side effect of registering every table on
    # SQLModel.metadata before create_all() runs. Metadata is only populated when
    # the model module has been imported, so a caller that gets here first would
    # otherwise build a database with no tables and then die in _migrate() on
    # "no such table: audiocache" — a startup crash. Imported inside the function
    # rather than at module scope so the call-time convention in CLAUDE.md holds
    # and no import cycle can form if db.models ever imports this module.
    import db.models  # noqa: F401  (registers tables on SQLModel.metadata)

    url = db_url or f"sqlite:///{_DEFAULT_DB}"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    _migrate(engine)
    return engine


def _migrate(engine):
    """Add columns that create_all() can't (existing tables)."""
    with engine.connect() as conn:
        ac_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(audiocache)"))}
        if 'word_timestamps' not in ac_cols:
            conn.execute(text("ALTER TABLE audiocache ADD COLUMN word_timestamps TEXT"))
            conn.commit()

        # Audio cache eviction (services/audio_cache.py) deletes the oldest rows
        # first, ordered by `created_at`. The only index audio cache had was the
        # implicit primary-key autoindex on `text_hash`, which cannot serve that
        # ORDER BY, so without this the sweep is a full table scan - and the
        # table holds ~800 MB of PCM. Unconditional under IF NOT EXISTS for the
        # same reason as ix_book_folder_id below: it is a no-op once the index
        # exists, which keeps _migrate idempotent and O(1) instead of rewriting
        # the database file on every boot.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_audiocache_created_at "
            "ON audiocache(created_at)"
        ))
        conn.commit()

        sent_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(sentence)"))}
        if 'words' not in sent_cols:
            conn.execute(text("ALTER TABLE sentence ADD COLUMN words TEXT"))
        if 'chapter' not in sent_cols:
            conn.execute(text("ALTER TABLE sentence ADD COLUMN chapter INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE sentence ADD COLUMN chapter_title TEXT DEFAULT NULL"))
        conn.commit()

        us_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(usersettings)"))}
        if 'highlight_enabled' not in us_cols:
            conn.execute(text("ALTER TABLE usersettings ADD COLUMN highlight_enabled INTEGER DEFAULT 1"))
            conn.commit()

        # Folders (handoff task 1). create_all() creates the new `folder` table on
        # both fresh and existing databases, but it never adds a column to a table
        # that already exists, so `book.folder_id` has to be added here. Without
        # this, every Book query against an existing database — the user's is
        # 800 MB and predates folders — fails with "no such column:
        # book.folder_id". The index is created unconditionally under IF NOT
        # EXISTS: on a fresh database create_all has already made it and this is a
        # no-op, which keeps the migration idempotent and stops it rewriting the
        # database file on every boot.
        book_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(book)"))}
        if 'folder_id' not in book_cols:
            conn.execute(text(
                "ALTER TABLE book ADD COLUMN folder_id INTEGER REFERENCES folder(id)"
            ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_book_folder_id ON book(folder_id)"
        ))
        conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
