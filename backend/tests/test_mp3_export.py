"""Tests for the MP3 export router.

The ``client`` fixture comes from conftest.py and wires the app to a per-test
in-memory database with a fake Kokoro pipeline, so an export can run to
completion without a GPU or a real 800 MB database. ``EXPORTS_DIR`` is redirected
under ``tmp_path`` by the isolation guard, so nothing is written into the repo.

Every export here asks for **WAV**, the one container `services.export_encoding`
writes through soundfile with no external process. The subject of this file is
the router's lifecycle -- creation, progress, listing, download, deletion -- not
the container, and asking for MP3 would make the suite's verdict depend on
whether the host has ffmpeg on PATH (conftest pins the probe off, so such a
request now answers 503 rather than silently succeeding here and failing on a
fresh container). Real MP3/M4B/Opus encoding, including tag verification, is
covered by `test_export_planning.py::TestEncodingWithRealFfmpeg`, which is
explicitly gated on the binary being installed.

The old version of this file asserted ``status in ("pending", "processing",
"done", "error")``, which passes for a permanently broken export, and never
checked that an export finished or produced a file. It does now.
"""
import time

import pytest
from sqlmodel import Session

from db.models import MP3Export
from routers import mp3 as mp3_router

TERMINAL_STATES = ("done", "error")
EXPORT_TIMEOUT = 30.0
REQUESTED_FORMAT = "wav"


def _drain_export_tasks(timeout: float = EXPORT_TIMEOUT) -> None:
    """Wait for every export task this test started to stop touching the database.

    ``_run_export`` offloads the work to a worker thread and the export path reads
    ``db.database.engine`` at call time. That engine is a *single* shared
    in-memory connection here, so a worker holding a session on it races every
    request the test itself makes: under CPU load this intermittently made an
    already-committed ``Book`` row invisible to the reader, which surfaced as a
    book title of "Unknown". A task that outlives its test is worse still — it
    wakes up after the isolation guard has restored that global, opens the next
    test's database, where export id 1 is a different row, and marks it ``error``.
    """
    deadline = time.monotonic() + timeout
    while mp3_router._export_tasks and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not mp3_router._export_tasks, "an export task did not finish in time"


@pytest.fixture(autouse=True)
def _drain_export_tasks_after_each_test():
    yield
    _drain_export_tasks()


def _upload(client, content=b"fakepdf"):
    response = client.post(
        "/documents/upload",
        files={"file": ("test.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["book_id"]


def _export_id(client, book_id, **overrides):
    body = {"book_id": book_id, "voice": "af_heart", "speed": 1.0,
            "format": REQUESTED_FORMAT}
    body.update(overrides)
    response = client.post("/mp3/export", json=body)
    assert response.status_code == 200, response.text
    return response.json()["export_id"]


def _await_terminal_status(client, export_id, timeout=EXPORT_TIMEOUT):
    """Poll /mp3/exports/{id}/status until the background task finishes."""
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/mp3/exports/{export_id}/status").json()
        if status["status"] in TERMINAL_STATES:
            return status
        time.sleep(0.05)
    raise AssertionError(f"export {export_id} never finished within {timeout}s: {status}")


class TestCreateExport:
    def test_create_export_returns_export_id(self, client):
        bid = _upload(client)
        r = client.post("/mp3/export", json={"book_id": bid, "voice": "af_heart",
                                            "speed": 1.0, "format": REQUESTED_FORMAT})
        assert r.status_code == 200
        assert "export_id" in r.json()
        # Let the background task finish here rather than after the test, which is
        # when the next test's database becomes "the" database.
        _await_terminal_status(client, r.json()["export_id"])

    def test_create_export_nonexistent_book_returns_404(self, client):
        # `format` matters even here: the router checks ffmpeg availability before
        # it looks the book up, so asking for MP3 on a host without ffmpeg would
        # answer 503 and this test would be measuring PATH rather than the 404.
        r = client.post("/mp3/export", json={"book_id": "nonexistent", "voice": "af_heart",
                                            "speed": 1.0, "format": REQUESTED_FORMAT})
        assert r.status_code == 404

    def test_export_completes_and_writes_a_downloadable_file(self, client, db_engine):
        """End to end: the export reaches ``done`` with a non-empty file on disk."""
        bid = _upload(client)
        export_id = _export_id(client, bid)

        status = _await_terminal_status(client, export_id)
        assert status["status"] == "done", status
        assert status["progress"] == 100
        assert status["file_size"] and status["file_size"] > 0

        with Session(db_engine) as session:
            row = session.get(MP3Export, export_id)
            assert row.status == "done"
            assert row.error_message is None
            assert row.format == REQUESTED_FORMAT
            file_path = row.file_path
        assert file_path, "a completed export must record where it wrote the file"

        from pathlib import Path

        written = Path(file_path)
        assert written.exists(), f"{written} was reported but is not on disk"
        assert written.stat().st_size == status["file_size"]

        download = client.get(f"/mp3/downloads/{export_id}")
        assert download.status_code == 200
        assert len(download.content) == status["file_size"]

    def test_export_progress_is_reported_while_running(self, client):
        """``pending`` is the state the client is handed before the task runs;
        the status endpoint must never report an unknown state."""
        bid = _upload(client)
        export_id = _export_id(client, bid)

        first = client.get(f"/mp3/exports/{export_id}/status").json()
        assert first["status"] in ("pending", "processing") + TERMINAL_STATES

        finished = _await_terminal_status(client, export_id)
        assert finished["status"] == "done", finished


class TestListExports:
    def test_list_exports_returns_empty_when_none(self, client):
        r = client.get("/mp3/exports")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_includes_book_title_and_status(self, client):
        bid = _upload(client)
        export_id = _export_id(client, bid)

        # Let the export this POST started finish before listing it. Both the list
        # endpoint and the worker use db.database.engine, which is one shared
        # in-memory connection, so listing while the worker holds a session on it
        # races -- under load this intermittently reported book_title "Unknown"
        # for a Book row that had already been committed. This test is about the
        # listing's contents, not its behaviour during a running export;
        # test_export_progress_is_reported_while_running covers that case and
        # deliberately asserts only that the status is a state the client
        # understands, rather than a specific one.
        _drain_export_tasks()

        rows = client.get("/mp3/exports").json()
        assert [row["id"] for row in rows] == [export_id]
        assert rows[0]["book_title"] == "test.pdf"
        assert rows[0]["voice"] == "af_heart"
        assert rows[0]["speed"] == 1.0
        _await_terminal_status(client, export_id)


class TestDeleteExport:
    def test_delete_completed_export_removes_its_file(self, client, db_engine):
        from pathlib import Path

        bid = _upload(client)
        export_id = _export_id(client, bid)
        _await_terminal_status(client, export_id)

        with Session(db_engine) as session:
            file_path = Path(session.get(MP3Export, export_id).file_path)
        assert file_path.exists()

        r = client.delete(f"/mp3/exports/{export_id}")
        assert r.status_code == 200

        assert not file_path.exists(), "deleting an export must delete its file"
        assert client.get("/mp3/exports").json() == []

    def test_delete_nonexistent_export_returns_404(self, client):
        r = client.delete("/mp3/exports/99999")
        assert r.status_code == 404
