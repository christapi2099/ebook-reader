import os
from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text

# DB_PATH env var allows Docker volume override; falls back to local file
_DEFAULT_DB = os.environ.get("DB_PATH") or str(Path(__file__).parent.parent / "ebook_reader.db")

engine = None


def create_engine_and_tables(db_url: str | None = None) -> object:
    global engine
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

        sent_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(sentence)"))}
        if 'words' not in sent_cols:
            conn.execute(text("ALTER TABLE sentence ADD COLUMN words TEXT"))
            conn.commit()

        us_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(usersettings)"))}
        if 'highlight_enabled' not in us_cols:
            conn.execute(text("ALTER TABLE usersettings ADD COLUMN highlight_enabled INTEGER DEFAULT 1"))
            conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
