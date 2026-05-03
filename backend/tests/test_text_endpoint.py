"""TDD tests for text book endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool
from datetime import datetime, UTC

from main import app
from db.database import get_session
from db.models import Book, Sentence


@pytest.fixture
def db_engine():
    from db.models import Book, Sentence, Progress
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(db_engine):
    def override_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


class TestCreateTextBook:
    def test_create_returns_200(self, client):
        r = client.post("/documents/text", json={"text": "This is a test sentence. This is another one."})
        assert r.status_code == 200

    def test_create_returns_book_id(self, client):
        r = client.post("/documents/text", json={"text": "This is a test sentence. This is another one."})
        data = r.json()
        assert "book_id" in data
        assert isinstance(data["book_id"], str)

    def test_create_returns_sentence_count(self, client):
        r = client.post("/documents/text", json={"text": "This is a test sentence. This is another one."})
        data = r.json()
        assert "sentence_count" in data
        assert data["sentence_count"] == 2

    def test_create_creates_book_with_file_type_text(self, client, db_engine):
        r = client.post("/documents/text", json={"text": "This is a test sentence."})
        data = r.json()
        book_id = data["book_id"]

        with Session(db_engine) as session:
            book = session.get(Book, book_id)
            assert book is not None
            assert book.file_type == "text"

    def test_create_creates_ephemeral_book_by_default(self, client, db_engine):
        r = client.post("/documents/text", json={"text": "This is a test sentence."})
        data = r.json()
        book_id = data["book_id"]

        with Session(db_engine) as session:
            book = session.get(Book, book_id)
            assert book is not None
            assert book.ephemeral is True

    def test_create_sentences_stored_with_zero_coordinates(self, client, db_engine):
        r = client.post("/documents/text", json={"text": "This is a test sentence."})
        book_id = r.json()["book_id"]

        with Session(db_engine) as session:
            sentences = session.exec(
                select(Sentence).where(Sentence.book_id == book_id)
            ).all()
            assert len(sentences) == 1
            s = sentences[0]
            assert s.page == 0
            assert s.x0 == 0.0
            assert s.y0 == 0.0
            assert s.x1 == 0.0
            assert s.y1 == 0.0

    def test_empty_text_returns_400(self, client):
        r = client.post("/documents/text", json={"text": ""})
        assert r.status_code == 400

    def test_whitespace_only_returns_400(self, client):
        r = client.post("/documents/text", json={"text": "   \n  "})
        assert r.status_code == 400

    def test_duplicate_text_returns_existing_book_id(self, client):
        text = "This is a unique test sentence."
        r1 = client.post("/documents/text", json={"text": text})
        book_id1 = r1.json()["book_id"]

        r2 = client.post("/documents/text", json={"text": text})
        book_id2 = r2.json()["book_id"]

        assert book_id1 == book_id2


class TestPersistTextBook:
    def test_persist_sets_ephemeral_false(self, client, db_engine):
        r = client.post("/documents/text", json={"text": "This is a test sentence."})
        book_id = r.json()["book_id"]

        # Verify it's ephemeral
        with Session(db_engine) as session:
            book = session.get(Book, book_id)
            assert book.ephemeral is True

        # Persist it
        r = client.patch(f"/documents/text/{book_id}")
        assert r.status_code == 200

        # Verify it's no longer ephemeral
        with Session(db_engine) as session:
            book = session.get(Book, book_id)
            assert book.ephemeral is False

    def test_persist_nonexistent_returns_404(self, client):
        r = client.patch("/documents/text/nonexistent")
        assert r.status_code == 404

    def test_persist_non_text_book_returns_400(self, client, db_engine):
        # Create a regular book
        with Session(db_engine) as session:
            book = Book(
                id="test-book",
                title="Test",
                file_path="/test.pdf",
                file_type="pdf",
                page_count=1,
                created_at=datetime.now(UTC),
            )
            session.add(book)
            session.commit()

        r = client.patch("/documents/text/test-book")
        assert r.status_code == 400


class TestGetBookEndpoint:
    def test_get_single_book_returns_metadata(self, client, db_engine):
        # Create a text book
        r = client.post("/documents/text", json={"text": "This is a test sentence."})
        book_id = r.json()["book_id"]

        # Get the book metadata
        r = client.get(f"/library/{book_id}")
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert "title" in data
        assert "file_type" in data

    def test_get_book_includes_file_type(self, client, db_engine):
        r = client.post("/documents/text", json={"text": "This is a test sentence."})
        book_id = r.json()["book_id"]

        r = client.get(f"/library/{book_id}")
        data = r.json()
        assert data["file_type"] == "text"


class TestCleanupEphemeral:
    def test_cleanup_deletes_old_ephemeral_books(self, client, db_engine):
        # Create an ephemeral book
        r = client.post("/documents/text", json={"text": "This is old ephemeral text."})
        book_id = r.json()["book_id"]

        # Manually set created_at to be older than 24h
        with Session(db_engine) as session:
            book = session.get(Book, book_id)
            book.created_at = datetime.now(UTC)
            session.add(book)
            session.commit()

        # Run cleanup (call the endpoint)
        r = client.delete("/documents/text/cleanup")
        assert r.status_code == 200

    def test_cleanup_presists_non_ephemeral_books(self, client, db_engine):
        # Create and persist a text book
        r = client.post("/documents/text", json={"text": "Saved text."})
        book_id = r.json()["book_id"]
        client.patch(f"/documents/text/{book_id}")

        # Book should still exist after cleanup
        r = client.get(f"/library/{book_id}")
        assert r.status_code == 200
