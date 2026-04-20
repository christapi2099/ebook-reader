"""TDD tests for db/models.py and db/database.py."""
import pytest
from datetime import datetime, UTC
from sqlmodel import Session, select

from db.database import create_engine_and_tables, get_session
from db.models import Book, Sentence, AudioCache, Progress


@pytest.fixture
def db_session():
    engine = create_engine_and_tables(db_url="sqlite:///:memory:")
    with Session(engine) as session:
        yield session


class TestBookModel:
    def test_create_book(self, db_session):
        book = Book(
            id="abc123",
            title="Clean Code",
            author="Robert C. Martin",
            file_path="/tmp/clean.pdf",
            file_type="pdf",
            page_count=464,
            created_at=datetime.now(UTC),
        )
        db_session.add(book)
        db_session.commit()

        result = db_session.get(Book, "abc123")
        assert result is not None
        assert result.title == "Clean Code"
        assert result.file_type == "pdf"

    def test_last_opened_is_nullable(self, db_session):
        book = Book(
            id="xyz", title="Test", file_path="/tmp/t.pdf",
            file_type="pdf", page_count=1, created_at=datetime.now(UTC),
        )
        db_session.add(book)
        db_session.commit()
        assert db_session.get(Book, "xyz").last_opened is None


class TestSentenceModel:
    def test_create_sentence(self, db_session):
        book = Book(id="b1", title="T", file_path="/t", file_type="pdf",
                    page_count=1, created_at=datetime.now(UTC))
        db_session.add(book)
        db_session.commit()

        s = Sentence(book_id="b1", index=0, text="Hello world.", page=0,
                     x0=10.0, y0=20.0, x1=200.0, y1=35.0)
        db_session.add(s)
        db_session.commit()

        result = db_session.exec(select(Sentence).where(Sentence.book_id == "b1")).first()
        assert result.text == "Hello world."
        assert result.filtered is False

    def test_filtered_flag(self, db_session):
        book = Book(id="b2", title="T", file_path="/t", file_type="pdf",
                    page_count=1, created_at=datetime.now(UTC))
        db_session.add(book)
        db_session.commit()

        s = Sentence(book_id="b2", index=0, text="Chapter 1", page=0,
                     x0=0.0, y0=0.0, x1=100.0, y1=15.0, filtered=True)
        db_session.add(s)
        db_session.commit()

        result = db_session.exec(select(Sentence).where(Sentence.book_id == "b2")).first()
        assert result.filtered is True


class TestAudioCacheModel:
    def test_create_cache_entry(self, db_session):
        entry = AudioCache(
            text_hash="hashval",
            audio_data=b"\x00\x01\x02",
            duration_ms=1200,
            voice="af_heart",
            created_at=datetime.now(UTC),
        )
        db_session.add(entry)
        db_session.commit()

        result = db_session.get(AudioCache, "hashval")
        assert result.duration_ms == 1200
        assert result.audio_data == b"\x00\x01\x02"


class TestProgressModel:
    def test_create_and_update_progress(self, db_session):
        book = Book(id="b3", title="T", file_path="/t", file_type="pdf",
                    page_count=1, created_at=datetime.now(UTC))
        db_session.add(book)
        db_session.commit()

        p = Progress(book_id="b3", sentence_index=42, updated_at=datetime.now(UTC))
        db_session.add(p)
        db_session.commit()

        result = db_session.get(Progress, "b3")
        assert result.sentence_index == 42

        result.sentence_index = 100
        db_session.add(result)
        db_session.commit()

        assert db_session.get(Progress, "b3").sentence_index == 100
