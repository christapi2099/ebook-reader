"""TDD tests for User Settings router."""
import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from main import app
from db.database import get_session
from db.models import UserSettings, Book


@pytest.fixture
def db_engine():
    """Create an in-memory database for testing."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(db_engine):
    """Create a test client with overridden session."""
    def override_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


class TestGetUserSettings:
    """Test GET /user/settings endpoint."""

    def test_get_default_settings(self, client):
        """Test that default settings are returned when no settings exist."""
        r = client.get("/user/settings")
        assert r.status_code == 200
        data = r.json()
        assert data["last_book_id"] is None
        assert data["last_sentence_index"] == 0

    def test_get_existing_settings(self, client, db_engine):
        """Test that existing settings are returned correctly."""
        # Create a test book first
        with Session(db_engine) as session:
            book = Book(
                id="test-book-id",
                title="Test Book",
                file_path="/test/path.pdf",
                file_type="pdf",
                page_count=10,
                created_at=datetime.now()
            )
            session.add(book)
            
            settings = UserSettings(
                id=1,
                last_book_id="test-book-id",
                last_sentence_index=42
            )
            session.add(settings)
            session.commit()
        
        r = client.get("/user/settings")
        assert r.status_code == 200
        data = r.json()
        assert data["last_book_id"] == "test-book-id"
        assert data["last_sentence_index"] == 42


class TestUpdateUserSettings:
    """Test POST /user/settings endpoint."""

    def test_create_settings(self, client):
        """Test creating new user settings."""
        r = client.post("/user/settings", json={
            "last_book_id": "new-book-id",
            "last_sentence_index": 10
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        
        # Verify settings were created
        r2 = client.get("/user/settings")
        assert r2.json()["last_book_id"] == "new-book-id"
        assert r2.json()["last_sentence_index"] == 10

    def test_update_existing_settings(self, client, db_engine):
        """Test updating existing user settings."""
        # Create initial settings
        with Session(db_engine) as session:
            settings = UserSettings(
                id=1,
                last_book_id="old-book-id",
                last_sentence_index=5
            )
            session.add(settings)
            session.commit()
        
        # Update settings
        r = client.post("/user/settings", json={
            "last_book_id": "updated-book-id",
            "last_sentence_index": 20
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        
        # Verify updates
        r2 = client.get("/user/settings")
        assert r2.json()["last_book_id"] == "updated-book-id"
        assert r2.json()["last_sentence_index"] == 20

    def test_partial_update(self, client, db_engine):
        """Test updating only one field of settings."""
        # Create initial settings
        with Session(db_engine) as session:
            settings = UserSettings(
                id=1,
                last_book_id="old-book-id",
                last_sentence_index=5
            )
            session.add(settings)
            session.commit()
        
        # Update only sentence index
        r = client.post("/user/settings", json={
            "last_sentence_index": 30
        })
        assert r.status_code == 200
        
        # Verify partial update (book_id should remain unchanged)
        r2 = client.get("/user/settings")
        assert r2.json()["last_book_id"] == "old-book-id"
        assert r2.json()["last_sentence_index"] == 30

    def test_update_with_null_book_id(self, client, db_engine):
        """Test setting last_book_id to null."""
        # First create settings with a book
        with Session(db_engine) as session:
            settings = UserSettings(
                id=1,
                last_book_id="some-book",
                last_sentence_index=5
            )
            session.add(settings)
            session.commit()
        
        # Update to null
        r = client.post("/user/settings", json={
            "last_book_id": None,
            "last_sentence_index": 10
        })
        assert r.status_code == 200
        
        # Verify null book_id
        r2 = client.get("/user/settings")
        assert r2.json()["last_book_id"] is None
        assert r2.json()["last_sentence_index"] == 10
