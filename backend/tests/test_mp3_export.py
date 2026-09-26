"""Tests for the MP3 export router.

The ``client`` fixture comes from conftest.py and wires the app to a per-test
in-memory database with a fake Kokoro pipeline, so an export can run to
completion without a GPU or a real 800 MB database. ``EXPORTS_DIR`` is redirected
under ``tmp_path`` by the isolation guard, so nothing is written into the repo.

The old version of this file asserted ``status in ("pending", "processing",
"done", "error")``, which passes for a permanently broken export, and never
checked that an export finished or produced a file. It does now.
"""
import time

from sqlmodel import Session

from db.models import MP3Export

TERMINAL_STATES = ("done", "error")
EXPORT_TIMEOUT = 30.0


def _upload(client, content=b"fakepdf"):
    response = client.post(
        "/documents/upload",
        files={"file": ("test.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["book_id"]


def _export_id(client, book_id, **overrides):
    body = {"book_id": book_id, "voice": "af_heart", "speed": 1.0}
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
        r = client.post("/mp3/export", json={"book_id": bid, "voice": "af_heart", "speed": 1.0})
        assert r.status_code == 200
        assert "export_id" in r.json()

    def test_create_export_nonexistent_book_returns_404(self, client):
        r = client.post("/mp3/export", json={"book_id": "nonexistent", "voice": "af_heart", "speed": 1.0})
        assert r.status_code == 404

    def test_export_completes_and_writes_a_downloadable_file(self, client, db_engine):
        """End to end: the export reaches ``done`` with a non-empty MP3 on disk."""
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

        rows = client.get("/mp3/exports").json()
        assert [row["id"] for row in rows] == [export_id]
        assert rows[0]["book_title"] == "test.pdf"
        assert rows[0]["voice"] == "af_heart"
        assert rows[0]["speed"] == 1.0


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
