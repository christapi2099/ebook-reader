"""TDD tests for text book endpoints.

The ``db_engine``/``client`` fixtures come from conftest.py.
"""
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from db.models import Book, Sentence


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
    def test_cleanup_deletes_ephemeral_books_older_than_24h(self, client, db_engine):
        """The endpoint deletes by age, so the row has to *be* older than 24 h.

        The old version set ``created_at = datetime.now(UTC)`` -- i.e. now -- and
        only asserted a 200, so it proved nothing about the cutoff.
        """
        r = client.post("/documents/text", json={"text": "This is old ephemeral text."})
        book_id = r.json()["book_id"]

        with Session(db_engine) as session:
            book = session.get(Book, book_id)
            book.created_at = datetime.now(UTC) - timedelta(hours=25)
            session.add(book)
            session.commit()

        r = client.delete("/documents/text/cleanup")
        assert r.status_code == 200
        assert r.json()["deleted_count"] == 1

        with Session(db_engine) as session:
            assert session.get(Book, book_id) is None, "the stale book must be gone"
            remaining = session.exec(
                select(Sentence).where(Sentence.book_id == book_id)
            ).all()
            assert remaining == [], "its sentences must be gone too"

    def test_cleanup_keeps_recent_ephemeral_books(self, client, db_engine):
        """The other half of the cutoff: a fresh ephemeral book must survive."""
        book_id = client.post("/documents/text", json={"text": "Fresh text here."}).json()["book_id"]

        r = client.delete("/documents/text/cleanup")

        assert r.json()["deleted_count"] == 0
        assert client.get(f"/library/{book_id}").status_code == 200

    def test_cleanup_presists_non_ephemeral_books(self, client, db_engine):
        # Create and persist a text book
        r = client.post("/documents/text", json={"text": "Saved text."})
        book_id = r.json()["book_id"]
        client.patch(f"/documents/text/{book_id}")

        with Session(db_engine) as session:
            book = session.get(Book, book_id)
            book.created_at = datetime.now(UTC) - timedelta(hours=25)
            session.add(book)
            session.commit()

        assert client.delete("/documents/text/cleanup").json()["deleted_count"] == 0

        # Book should still exist after cleanup
        r = client.get(f"/library/{book_id}")
        assert r.status_code == 200
