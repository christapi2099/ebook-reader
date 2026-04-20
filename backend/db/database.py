from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session

# Absolute path next to this file — reliable regardless of CWD at startup
_DEFAULT_DB = str(Path(__file__).parent.parent / "ebook_reader.db")

engine = None


def create_engine_and_tables(db_url: str | None = None) -> object:
    global engine
    url = db_url or f"sqlite:///{_DEFAULT_DB}"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    return engine


def get_session():
    with Session(engine) as session:
        yield session
