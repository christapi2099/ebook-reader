"""TDD tests for FastAPI routers. PDF/EPUB engines are mocked for speed."""
import pytest
import hashlib
from pathlib import Path
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, MagicMock

from main import app
from db.database import get_session
from services.pdf_engine import SentenceRecord as PdfSentence


def _fake_sentences(n=5):
    return [
        PdfSentence(index=i, text=f"This is sentence number {i} with enough words.", page=0,
                    x0=10.0, y0=float(i * 20), x1=400.0, y1=float(i * 20 + 15))
        for i in range(n)
    ]


@pytest.fixture
def db_engine():
    from db.models import Book, Sentence, AudioCache, Progress
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(db_engine):
    def override_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    with patch("routers.documents.PDFEngine") as mock_pdf, \
         patch("routers.documents.EPUBEngine") as mock_epub:
        mock_pdf.return_value.extract_sentences.return_value = _fake_sentences()
        mock_pdf.return_value.page_count.return_value = 10
        mock_epub.return_value.extract_sentences.return_value = []

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    app.dependency_overrides.clear()


def _upload(client, filename="test.pdf", content=b"fakepdfbytes"):
    return client.post(
        "/documents/upload",
        files={"file": (filename, content, "application/pdf")},
    )


class TestUploadDocument:
    def test_upload_pdf_returns_200(self, client):
        r = _upload(client)
        assert r.status_code == 200

    def test_upload_returns_book_id(self, client):
        r = _upload(client)
        data = r.json()
        assert "book_id" in data
        assert len(data["book_id"]) == 64

    def test_upload_returns_sentence_count(self, client):
        r = _upload(client)
        assert r.json()["sentence_count"] == 5

    def test_duplicate_upload_returns_same_id(self, client):
        ids = [_upload(client, content=b"samebytes").json()["book_id"] for _ in range(2)]
        assert ids[0] == ids[1]

    def test_duplicate_sets_already_existed(self, client):
        _upload(client, content=b"dupbytes")
        r2 = _upload(client, content=b"dupbytes")
        assert r2.json()["already_existed"] is True

    def test_unsupported_type_returns_400(self, client):
        r = client.post("/documents/upload", files={"file": ("book.txt", b"text", "text/plain")})
        assert r.status_code == 400


class TestGetSentences:
    @pytest.fixture
    def book_id(self, client):
        return _upload(client).json()["book_id"]

    def test_get_sentences_returns_list(self, client, book_id):
        r = client.get(f"/documents/{book_id}/sentences")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_sentences_have_required_fields(self, client, book_id):
        r = client.get(f"/documents/{book_id}/sentences")
        s = r.json()[0]
        for field in ("index", "text", "page", "x0", "y0", "x1", "y1", "filtered"):
            assert field in s

    def test_unknown_book_returns_404(self, client):
        assert client.get("/documents/nonexistent/sentences").status_code == 404


class TestLibrary:
    def test_list_library_returns_list(self, client):
        _upload(client)
        r = client.get("/library")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_library_contains_uploaded_book(self, client):
        book_id = _upload(client).json()["book_id"]
        ids = [b["id"] for b in client.get("/library").json()]
        assert book_id in ids


class TestProgress:
    @pytest.fixture
    def book_id(self, client):
        return _upload(client).json()["book_id"]

    def test_save_progress(self, client, book_id):
        r = client.post(f"/library/{book_id}/progress", json={"sentence_index": 42})
        assert r.status_code == 200

    def test_get_progress(self, client, book_id):
        client.post(f"/library/{book_id}/progress", json={"sentence_index": 42})
        r = client.get(f"/library/{book_id}/progress")
        assert r.status_code == 200
        assert r.json()["sentence_index"] == 42

    def test_no_progress_returns_zero(self, client, book_id):
        r = client.get(f"/library/{book_id}/progress")
        assert r.json()["sentence_index"] == 0
