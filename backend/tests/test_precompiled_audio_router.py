"""Tests for the precompiled-chapter-audio router.

Mirrors ``test_mp3_export.py``'s shape: the ``client``/``seed_book``/``real_ffmpeg``
fixtures come from conftest.py, ``CHAPTER_AUDIO_DIR`` is redirected under
``tmp_path`` by the isolation guard, and every build here genuinely runs ffmpeg
(opus encode + atempo are both mandatory for this feature, unlike MP3 export's
optional WAV-without-ffmpeg path) so ``real_ffmpeg`` is requested everywhere.

The subject is the router's job lifecycle (build/poll/serve/delete) and the
idempotency contract (a chapter already ``done`` must not be re-touched by a
later build call) — not manifest timing accuracy, which the real Kokoro engine
and ffmpeg's atempo filter own, not this test double.
"""
import time

import pytest
from sqlmodel import Session, select

from db.models import ChapterAudio
from routers import precompiled_audio as precompiled_router

TERMINAL_STATES = ("done", "error")
BUILD_TIMEOUT = 30.0


def _drain_precompile_tasks(timeout: float = BUILD_TIMEOUT) -> None:
    """Wait for every precompile task this test started to stop touching the DB.

    Same reasoning as ``test_mp3_export.py::_drain_export_tasks``: the worker
    thread and the test's own requests share one database, so a task that
    outlives its test can corrupt the next test's data.
    """
    deadline = time.monotonic() + timeout
    while precompiled_router._precompile_tasks and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not precompiled_router._precompile_tasks, "a precompile task did not finish in time"


@pytest.fixture(autouse=True)
def _drain_precompile_tasks_after_each_test():
    yield
    _drain_precompile_tasks()


def _build(client, book_id, chapter=0, **overrides):
    body = {"voice": "af_heart", "speeds": [1.0, 1.5]}
    body.update(overrides)
    response = client.post(f"/precompiled/{book_id}/chapters/{chapter}/build", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _await_all_terminal(client, book_id, chapter=0, voice="af_heart", timeout=BUILD_TIMEOUT):
    deadline = time.monotonic() + timeout
    rows = None
    while time.monotonic() < deadline:
        rows = client.get(
            f"/precompiled/{book_id}/chapters/{chapter}/status", params={"voice": voice}
        ).json()
        if rows and all(r["status"] in TERMINAL_STATES for r in rows):
            return rows
        time.sleep(0.05)
    raise AssertionError(f"precompile for {book_id}/{chapter} never finished within {timeout}s: {rows}")


class TestBuildChapterAudio:
    def test_build_nonexistent_book_returns_404(self, client, real_ffmpeg):
        r = client.post(
            "/precompiled/nonexistent/chapters/0/build",
            json={"voice": "af_heart", "speeds": [1.0]},
        )
        assert r.status_code == 404

    def test_build_without_ffmpeg_returns_503(self, client):
        # No `real_ffmpeg` fixture here: the autouse `_hermetic_ffmpeg` guard
        # pins ffmpeg_available() to False, same as a machine with no ffmpeg
        # on PATH.
        r = client.post(
            "/precompiled/anything/chapters/0/build",
            json={"voice": "af_heart", "speeds": [1.0]},
        )
        assert r.status_code == 503

    def test_build_completes_native_and_atempo_tiers(self, client, db_engine, seed_book, real_ffmpeg):
        seed_book(db_engine, book_id="book-1")

        started = _build(client, "book-1", speeds=[1.0, 1.5, 2.0])
        assert started["status"] == "started"
        assert sorted(started["tiers"]) == [1.0, 1.5, 2.0]

        rows = _await_all_terminal(client, "book-1")
        by_speed = {r["requested_speed"]: r for r in rows}
        assert set(by_speed) == {1.0, 1.5, 2.0}

        native = by_speed[1.0]
        assert native["status"] == "done", native
        assert native["method"] == "native"
        assert native["duration_ms"] and native["duration_ms"] > 0

        for speed in (1.5, 2.0):
            tier = by_speed[speed]
            assert tier["status"] == "done", tier
            assert tier["method"] == "atempo"
            assert tier["duration_ms"] and tier["duration_ms"] > 0
            # atempo speeds up playback, so the 1.5x/2.0x file must be
            # shorter than the native 1x file it was derived from.
            assert tier["duration_ms"] < native["duration_ms"]

        with Session(db_engine) as session:
            for row in session.exec(select(ChapterAudio).where(ChapterAudio.book_id == "book-1")).all():
                assert row.audio_path and row.manifest_path
                from pathlib import Path
                assert Path(row.audio_path).exists()
                assert Path(row.manifest_path).exists()

    def test_rebuild_does_not_retouch_already_done_tiers(self, client, db_engine, seed_book, real_ffmpeg):
        seed_book(db_engine, book_id="book-2")
        _build(client, "book-2", speeds=[1.0, 1.5])
        first_rows = _await_all_terminal(client, "book-2")
        assert all(r["status"] == "done" for r in first_rows)

        # Re-request the same tiers plus one new one: only the new tier
        # should be (re)built, and the already-done ones must be untouched.
        second = _build(client, "book-2", speeds=[1.0, 1.5, 2.5])
        assert second["tiers"] == [2.5]

        second_rows = _await_all_terminal(client, "book-2")
        assert {r["requested_speed"] for r in second_rows} == {1.0, 1.5, 2.5}
        assert all(r["status"] == "done" for r in second_rows)

    def test_rebuild_with_nothing_new_is_a_noop(self, client, db_engine, seed_book, real_ffmpeg):
        seed_book(db_engine, book_id="book-3")
        _build(client, "book-3", speeds=[1.0])
        _await_all_terminal(client, "book-3")

        r = client.post(
            "/precompiled/book-3/chapters/0/build",
            json={"voice": "af_heart", "speeds": [1.0]},
        )
        assert r.status_code == 200
        assert r.json() == {"status": "already_done"}

    def test_build_chapter_with_no_sentences_errors_every_tier(self, client, db_engine, seed_book, real_ffmpeg):
        seed_book(db_engine, book_id="book-4", sentences=[])
        _build(client, "book-4", speeds=[1.0, 1.5])
        rows = _await_all_terminal(client, "book-4")
        assert all(r["status"] == "error" for r in rows), rows
        assert all(r["error_message"] for r in rows)


class TestServeChapterAudio:
    def test_manifest_and_audio_not_ready_return_404(self, client, db_engine, seed_book):
        seed_book(db_engine, book_id="book-5")
        m = client.get("/precompiled/book-5/chapters/0/manifest", params={"voice": "af_heart"})
        assert m.status_code == 404
        a = client.get("/precompiled/book-5/chapters/0/audio", params={"voice": "af_heart"})
        assert a.status_code == 404

    def test_manifest_and_audio_serve_once_done(self, client, db_engine, seed_book, real_ffmpeg):
        seed_book(db_engine, book_id="book-6")
        _build(client, "book-6", speeds=[1.0])
        _await_all_terminal(client, "book-6")

        m = client.get("/precompiled/book-6/chapters/0/manifest", params={"voice": "af_heart", "speed": 1.0})
        assert m.status_code == 200
        manifest = m.json()
        assert manifest["method"] == "native"
        assert len(manifest["segments"]) == 3  # default_sentences() has 3 rows
        assert manifest["segments"][0]["start_ms"] == 0

        a = client.get("/precompiled/book-6/chapters/0/audio", params={"voice": "af_heart", "speed": 1.0})
        assert a.status_code == 200
        assert a.headers["content-type"] == "audio/ogg"
        assert len(a.content) > 0

    def test_audio_endpoint_supports_range_requests(self, client, db_engine, seed_book, real_ffmpeg):
        """`<audio>` seeking depends on Range support — a regression here would
        silently force every seek to re-download the whole chapter first."""
        seed_book(db_engine, book_id="book-7")
        _build(client, "book-7", speeds=[1.0])
        _await_all_terminal(client, "book-7")

        full = client.get("/precompiled/book-7/chapters/0/audio", params={"voice": "af_heart", "speed": 1.0})
        ranged = client.get(
            "/precompiled/book-7/chapters/0/audio",
            params={"voice": "af_heart", "speed": 1.0},
            headers={"Range": "bytes=0-99"},
        )
        assert ranged.status_code == 206
        assert len(ranged.content) == 100
        assert ranged.headers["content-range"] == f"bytes 0-99/{len(full.content)}"


class TestDeleteChapterAudio:
    def test_delete_removes_rows_and_files(self, client, db_engine, seed_book, real_ffmpeg):
        from pathlib import Path

        seed_book(db_engine, book_id="book-8")
        _build(client, "book-8", speeds=[1.0])
        _await_all_terminal(client, "book-8")
        with Session(db_engine) as session:
            file_paths = [
                Path(row.audio_path)
                for row in session.exec(select(ChapterAudio).where(ChapterAudio.book_id == "book-8")).all()
            ]
        assert all(p.exists() for p in file_paths)

        d = client.delete("/precompiled/book-8/chapters/0", params={"voice": "af_heart"})
        assert d.status_code == 200
        assert not any(p.exists() for p in file_paths)

        status = client.get("/precompiled/book-8/chapters/0/status", params={"voice": "af_heart"})
        assert status.json() == []
