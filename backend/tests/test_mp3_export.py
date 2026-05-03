"""Tests for the MP3 export router."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

import db.database as _db
from routers import mp3 as mp3_router
from routers import documents as documents_router
from services.pdf_engine import SentenceRecord as PdfSentence

SAMPLE_SENTENCES = [
    PdfSentence(index=i, text=f"Sentence {i}.", page=i % 2,
                x0=10.0, y0=float(i * 20), x1=400.0, y1=float(i * 20 + 15))
    for i in range(5)
]


def _make_engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _make_app():
    app = FastAPI()
    app.include_router(documents_router.router)
    app.include_router(mp3_router.router)
    return app


@pytest.fixture
def db_engine():
    eng = _make_engine()
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(db_engine):
    _db.engine = db_engine
    app = _make_app()

    with patch("routers.documents.PDFEngine") as mock_pdf, \
         patch("routers.documents.EPUBEngine") as mock_epub:
        mock_pdf.return_value.extract_sentences.return_value = SAMPLE_SENTENCES
        mock_pdf.return_value.page_count.return_value = 5
        mock_epub.return_value.extract_sentences.return_value = []

        with TestClient(app) as c:
            yield c


def _upload(client):
    return client.post(
        "/documents/upload",
        files={"file": ("test.pdf", b"fakepdf", "application/pdf")},
    ).json()["book_id"]


class TestCreateExport:
    def test_create_export_returns_export_id(self, client):
        bid = _upload(client)
        r = client.post("/mp3/export", json={"book_id": bid, "voice": "af_heart", "speed": 1.0})
        assert r.status_code == 200
        assert "export_id" in r.json()

    def test_create_export_nonexistent_book_returns_404(self, client):
        r = client.post("/mp3/export", json={"book_id": "nonexistent", "voice": "af_heart", "speed": 1.0})
        assert r.status_code == 404

    def test_new_export_has_valid_status(self, client):
        bid = _upload(client)
        r = client.post("/mp3/export", json={"book_id": bid, "voice": "af_heart", "speed": 1.0})
        eid = r.json()["export_id"]
        r2 = client.get(f"/mp3/exports/{eid}/status")
        status = r2.json()["status"]
        assert status in ("pending", "processing", "done", "error"), f"Unexpected status: {status}"


class TestListExports:
    def test_list_exports_returns_empty_when_none(self, client):
        r = client.get("/mp3/exports")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_includes_book_title(self, client):
        bid = _upload(client)
        client.post("/mp3/export", json={"book_id": bid, "voice": "af_heart", "speed": 1.0})
        r = client.get("/mp3/exports")
        assert len(r.json()) >= 1
        assert r.json()[0]["book_title"] == "test.pdf"


class TestDeleteExport:
    def test_delete_existing_export(self, client):
        bid = _upload(client)
        r = client.post("/mp3/export", json={"book_id": bid, "voice": "af_heart", "speed": 1.0})
        eid = r.json()["export_id"]
        r2 = client.delete(f"/mp3/exports/{eid}")
        assert r2.status_code == 200

    def test_delete_nonexistent_export_returns_404(self, client):
        r = client.delete("/mp3/exports/99999")
        assert r.status_code == 404
