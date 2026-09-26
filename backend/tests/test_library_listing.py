"""`GET /library` must carry the two fields reading progress actually needs.

`page_count` is not a usable denominator: it means PDF pages, or a sentence count,
or a derived ``len(text) // 10`` depending on ``file_type``. Before this, the only
endpoint that revealed a book's sentence total was
``GET /documents/{id}/sentences``, which returns every sentence *with word
bounding boxes* — one such request per started book, on a page that should cost
one request, to read a single integer.

The distinction these tests pin hardest is ``None`` versus ``0``: the API reports
``sentence_index = 0`` both for "never started" and for "parked on the first
sentence", and a client cannot tell a 0% bar from no bar without a null.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from db.database import get_session
from db.models import Book, Progress, Sentence
from main import app


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(db_engine):
    def override_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


@pytest.fixture
def add_book(db_engine):
    def _add(book_id="bk1", sentences=0, progress=None, page_count=99):
        with Session(db_engine) as session:
            session.add(Book(
                id=book_id, title=book_id, file_path=f"/{book_id}.pdf",
                file_type="pdf", page_count=page_count,
                created_at=datetime.now(timezone.utc),
            ))
            for i in range(sentences):
                session.add(Sentence(
                    book_id=book_id, index=i, text=f"Sentence {i}.", page=0,
                    x0=0.0, y0=0.0, x1=1.0, y1=1.0,
                ))
            if progress is not None:
                session.add(Progress(
                    book_id=book_id, sentence_index=progress,
                    updated_at=datetime.now(timezone.utc),
                ))
            session.commit()
        return book_id

    return _add


class TestLibraryListingCarriesProgressFields:
    def test_reports_the_sentence_total(self, client, add_book):
        add_book(sentences=5)
        row = client.get("/library").json()[0]
        assert row["sentence_count"] == 5

    def test_a_book_with_no_sentences_reports_zero(self, client, add_book):
        add_book(sentences=0)
        assert client.get("/library").json()[0]["sentence_count"] == 0

    def test_reports_no_position_when_never_started(self, client, add_book):
        add_book(sentences=5)
        assert client.get("/library").json()[0]["sentence_index"] is None

    def test_reports_the_saved_position(self, client, add_book):
        add_book(sentences=5, progress=3)
        assert client.get("/library").json()[0]["sentence_index"] == 3

    def test_position_zero_is_distinguishable_from_never_started(self, client, add_book):
        """"parked on the first sentence" must not look like "no position"."""
        add_book("started", sentences=5, progress=0)
        add_book("untouched", sentences=5)
        rows = {r["id"]: r for r in client.get("/library").json()}
        assert rows["started"]["sentence_index"] == 0
        assert rows["untouched"]["sentence_index"] is None

    def test_the_count_is_per_book(self, client, add_book):
        add_book("small", sentences=2)
        add_book("large", sentences=7)
        rows = {r["id"]: r["sentence_count"] for r in client.get("/library").json()}
        assert rows == {"small": 2, "large": 7}

    def test_page_count_is_not_the_sentence_total(self, client, add_book):
        """The whole reason these fields exist: page_count is a different unit."""
        add_book(sentences=5, page_count=99)
        row = client.get("/library").json()[0]
        assert row["page_count"] == 99
        assert row["sentence_count"] == 5

    def test_ephemeral_books_are_still_excluded(self, client, db_engine, add_book):
        add_book("kept", sentences=1)
        with Session(db_engine) as session:
            session.add(Book(
                id="ephemeral", title="scratch", file_path="/x.txt",
                file_type="text", page_count=1, ephemeral=True,
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()
        assert [r["id"] for r in client.get("/library").json()] == ["kept"]

    def test_the_single_book_endpoint_carries_the_same_fields(self, client, add_book):
        add_book(sentences=4, progress=2)
        row = client.get("/library/bk1").json()
        assert row["sentence_count"] == 4
        assert row["sentence_index"] == 2

    def test_single_book_reports_no_position_when_never_started(self, client, add_book):
        add_book(sentences=4)
        assert client.get("/library/bk1").json()["sentence_index"] is None
