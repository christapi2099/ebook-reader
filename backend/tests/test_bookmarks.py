"""Tests for the bookmarks router."""
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
        PdfSentence(index=i, text=f"This is sentence number {i}.", page=i,
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
        mock_pdf.return_value.page_count.return_value = 3
        mock_epub.return_value.extract_sentences.return_value = []

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    app.dependency_overrides.clear()


def _upload(client):
    return client.post(
        "/documents/upload",
        files={"file": ("test.pdf", b"fakepdf", "application/pdf")},
    ).json()["book_id"]


class TestCreateBookmark:
    def test_create_with_valid_book_returns_bookmark(self, client):
        bid = _upload(client)
        r = client.post("/bookmarks", json={
            "book_id": bid, "sentence_index": 2, "label": "Test bookmark"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["book_id"] == bid
        assert data["sentence_index"] == 2
        assert data["label"] == "Test bookmark"
        assert data["page"] >= 0

    def test_create_nonexistent_book_returns_404(self, client):
        r = client.post("/bookmarks", json={
            "book_id": "nonexistent", "sentence_index": 0, "label": "Bad"
        })
        assert r.status_code == 404

    def test_label_truncated_to_100_chars(self, client):
        bid = _upload(client)
        long_label = "x" * 200
        r = client.post("/bookmarks", json={
            "book_id": bid, "sentence_index": 0, "label": long_label
        })
        assert len(r.json()["label"]) == 100


class TestListBookmarks:
    def test_list_empty_returns_empty_list(self, client):
        bid = _upload(client)
        r = client.get(f"/bookmarks/{bid}")
        assert r.json() == []

    def test_list_returns_bookmarks_in_order(self, client):
        bid = _upload(client)
        client.post("/bookmarks", json={"book_id": bid, "sentence_index": 3, "label": "Third"})
        client.post("/bookmarks", json={"book_id": bid, "sentence_index": 1, "label": "First"})
        r = client.get(f"/bookmarks/{bid}")
        assert len(r.json()) == 2


class TestDeleteBookmark:
    def test_delete_existing_returns_200(self, client):
        bid = _upload(client)
        r = client.post("/bookmarks", json={"book_id": bid, "sentence_index": 0, "label": "X"})
        bm_id = r.json()["id"]
        r2 = client.delete(f"/bookmarks/{bm_id}")
        assert r2.status_code == 200

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/bookmarks/99999")
        assert r.status_code == 404

    def test_delete_removes_from_list(self, client):
        bid = _upload(client)
        r = client.post("/bookmarks", json={"book_id": bid, "sentence_index": 0, "label": "X"})
        bm_id = r.json()["id"]
        client.delete(f"/bookmarks/{bm_id}")
        bookmarks = client.get(f"/bookmarks/{bid}").json()
        ids = [b["id"] for b in bookmarks]
        assert bm_id not in ids
