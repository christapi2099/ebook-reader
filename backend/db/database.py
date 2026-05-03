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
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(audiocache)"))}
        if 'word_timestamps' not in cols:
            conn.execute(text("ALTER TABLE audiocache ADD COLUMN word_timestamps TEXT"))
            conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
