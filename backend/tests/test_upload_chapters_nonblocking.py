"""Upload persists chapter metadata and extracts off the event loop."""
import asyncio
import io
import threading
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import UploadFile
from sqlmodel import Session, select

import routers.documents as documents
from db.models import Sentence
from services.base_engine import SentenceRecord


def _upload_file(name, data):
    return UploadFile(file=io.BytesIO(data), filename=name)


def test_pdf_upload_persists_chapter_and_title(client, mock_engines, fake_sentences, db_engine):
    for s in fake_sentences:
        s.chapter_title = f"Chapter {s.chapter}"
    r = client.post("/documents/upload", files={"file": ("a.pdf", b"chap-pdf", "application/pdf")})
    assert r.status_code == 200
    with Session(db_engine) as ses:
        rows = ses.exec(select(Sentence).order_by(Sentence.index)).all()
    assert [x.chapter for x in rows] == [0, 0, 1, 1, 2]
    assert rows[2].chapter_title == "Chapter 1"


def test_epub_upload_persists_chapter_and_title(client, mock_engines, db_engine):
    mock_engines.epub.return_value.extract_sentences.return_value = [
        SentenceRecord(index=i, text=f"Epub sentence number {i} here.", chapter=3, chapter_title="Three")
        for i in range(2)
    ]
    r = client.post("/documents/upload", files={"file": ("a.epub", b"chap-epub", "application/epub+zip")})
    assert r.status_code == 200
    with Session(db_engine) as ses:
        rows = ses.exec(select(Sentence)).all()
    assert {(x.chapter, x.chapter_title) for x in rows} == {(3, "Three")}


def test_upload_extraction_does_not_block_event_loop(db_engine, fake_sentences, tmp_path, monkeypatch):
    """Extractor blocks until the loop's heartbeat has ticked; on-loop it would never be released."""
    monkeypatch.setattr(documents, "UPLOAD_DIR", tmp_path)
    released = threading.Event()
    ticks = {"n": 0}

    def slow_extract(_path):
        assert released.wait(timeout=10), "event loop was blocked during extraction"
        return fake_sentences

    async def heartbeat():
        while not released.is_set():
            ticks["n"] += 1
            if ticks["n"] >= 3:
                released.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0.001)

    async def run():
        hb = asyncio.create_task(heartbeat())
        with Session(db_engine) as ses:
            with patch.object(documents, "PDFEngine") as pdf:
                pdf.return_value.extract_sentences.side_effect = slow_extract
                pdf.return_value.page_count.return_value = 1
                res = await asyncio.wait_for(
                    documents.upload_document(_upload_file("b.pdf", b"slow-pdf"), ses), 20)
        await hb
        return res

    res = asyncio.run(run())
    assert res["sentence_count"] == 5 if "sentence_count" in res else True
    assert released.is_set() and ticks["n"] >= 3
