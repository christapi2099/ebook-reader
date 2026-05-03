"""Tests for DELETE /library/{book_id} endpoint."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from main import app
from db.database import get_session
from services.base_engine import SentenceRecord as PdfSentence


def _fake_sentences(n=5):
    return [
        PdfSentence(index=i, text=f"This is sentence number {i}.", page=0,
                    x0=10.0, y0=float(i * 20), x1=400.0, y1=float(i * 20 + 15))
        for i in range(n)
    ]


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

    with patch("routers.documents.PDFEngine") as mock_pdf, \
         patch("routers.documents.EPUBEngine") as mock_epub:
        mock_pdf.return_value.extract_sentences.return_value = _fake_sentences()
        mock_pdf.return_value.page_count.return_value = 10
        mock_epub.return_value.extract_sentences.return_value = []

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    app.dependency_overrides.clear()


def _upload(client, filename="test.pdf", content=b"fakepdf"):
    return client.post(
        "/documents/upload",
        files={"file": (filename, content, "application/pdf")},
    )


class TestDeleteBook:
    def test_delete_existing_book_returns_200(self, client):
        data = _upload(client).json()
        r = client.delete(f"/library/{data['book_id']}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_removes_book_from_library(self, client):
        data = _upload(client).json()
        client.delete(f"/library/{data['book_id']}")
        books = client.get("/library").json()
        ids = [b["id"] for b in books]
        assert data["book_id"] not in ids

    def test_delete_removes_progress(self, client):
        data = _upload(client).json()
        bid = data["book_id"]
        client.post(f"/library/{bid}/progress", json={"sentence_index": 10})
        client.delete(f"/library/{bid}")
        r = client.get(f"/library/{bid}/progress")
        assert r.json()["sentence_index"] == 0

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/library/nonexistent")
        assert r.status_code == 404

    def test_delete_sentences_removed(self, client):
        data = _upload(client).json()
        bid = data["book_id"]
        assert len(client.get(f"/documents/{bid}/sentences").json()) == 5
        client.delete(f"/library/{bid}")
        r = client.get(f"/documents/{bid}/sentences")
        assert r.status_code == 404
