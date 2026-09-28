"""Tests for the precompiled-chapter-audio router.

Mirrors ``test_mp3_export.py``'s shape: the ``client``/``seed_book``/``real_ffmpeg``
fixtures come from conftest.py, ``CHAPTER_AUDIO_DIR`` is redirected under
``tmp_path`` by the isolation guard, and every build here genuinely runs ffmpeg
(opus encode + atempo are both mandatory for this feature, unlike MP3 export's
optional WAV-without-ffmpeg path) so ``real_ffmpeg`` is requested everywhere.

The subject is the router's job lifecycle (build/poll/serve/delete), the
idempotency contract (a chapter already ``done`` must not be re-touched by a
later build call), and the global one-at-a-time queue (found necessary after
a real CUDA OOM hit manually testing two chapters precompiling concurrently)
— not manifest timing accuracy, which the real Kokoro engine and ffmpeg's
atempo filter own, not this test double.

The queue worker itself needs no special test setup: `main.py`'s lifespan
starts it (`precompiled_audio_router.start_queue_worker()`), and the
`client` fixture (conftest.py) enters `TestClient` as a context manager,
which runs that lifespan on TestClient's own event loop — the same loop
every request in this file runs on. A hand-rolled second loop/thread was
tried here first and is exactly the kind of thing to avoid: `asyncio.Queue`
is not safe to `put()` from one loop and `get()` from another.
"""
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, select

from db.models import ChapterAudio
from routers import precompiled_audio as precompiled_router

TERMINAL_STATES = ("done", "error")
BUILD_TIMEOUT = 30.0


def _await_queue_drained(timeout: float = BUILD_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while precompiled_router._queue_order and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not precompiled_router._queue_order, "a precompile job did not finish in time"


@pytest.fixture(autouse=True)
def _drain_queue_after_each_test():
    yield
    _await_queue_drained()


def _build(client, book_id, chapter=0, **overrides):
    body = {"voice": "af_heart", "speeds": [1.0, 1.5]}
    body.update(overrides)
    response = client.post(f"/precompiled/{book_id}/chapters/{chapter}/build", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _status(client, book_id, chapter=0, voice="af_heart"):
    response = client.get(f"/precompiled/{book_id}/chapters/{chapter}/status", params={"voice": voice})
    assert response.status_code == 200, response.text
    return response.json()


def _await_all_terminal(client, book_id, chapter=0, voice="af_heart", timeout=BUILD_TIMEOUT):
    deadline = time.monotonic() + timeout
    data = None
    while time.monotonic() < deadline:
        data = _status(client, book_id, chapter, voice)
        rows = data["tiers"]
        if rows and all(r["status"] in TERMINAL_STATES for r in rows):
            return rows
        time.sleep(0.05)
    raise AssertionError(f"precompile for {book_id}/{chapter} never finished within {timeout}s: {data}")


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
        assert started["status"] == "queued"
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

    def test_second_build_for_same_chapter_reports_already_queued(self, client, db_engine, seed_book, real_ffmpeg):
        seed_book(db_engine, book_id="book-9")
        _build(client, "book-9", speeds=[1.0])
        # Fired again immediately, before the worker has necessarily finished
        # — must not enqueue a second job for the same (book, chapter, voice).
        second = client.post(
            "/precompiled/book-9/chapters/0/build",
            json={"voice": "af_heart", "speeds": [1.0]},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "already_queued"
        _await_all_terminal(client, "book-9")


class TestQueueSerialization:
    def test_jobs_run_one_at_a_time_in_fifo_order(self, client, db_engine, seed_book, real_ffmpeg):
        """Two chapters precompiling concurrently caused a real CUDA OOM
        (hit manually testing this feature) — this asserts the fix: only one
        job is ever `processing` across the whole queue at once."""
        seed_book(db_engine, book_id="book-10")
        seed_book(db_engine, book_id="book-11")

        first = _build(client, "book-10", speeds=[1.0])
        second = _build(client, "book-11", speeds=[1.0])
        assert first["status"] == "queued"
        assert second["status"] == "queued"

        # The second job's queue position must reflect it's behind the first,
        # at least until the first finishes (this is a race with the worker,
        # so only assert the ordering invariant, not an exact position).
        data = _status(client, "book-11")
        assert data["queue_position"] is None or data["queue_position"] >= 0

        deadline = time.monotonic() + BUILD_TIMEOUT
        saw_two_processing_at_once = False
        while time.monotonic() < deadline:
            rows_10 = _status(client, "book-10")["tiers"]
            rows_11 = _status(client, "book-11")["tiers"]
            processing_10 = any(r["status"] == "processing" for r in rows_10)
            processing_11 = any(r["status"] == "processing" for r in rows_11)
            if processing_10 and processing_11:
                saw_two_processing_at_once = True
                break
            if all(r["status"] in TERMINAL_STATES for r in rows_10) and all(
                r["status"] in TERMINAL_STATES for r in rows_11
            ):
                break
            time.sleep(0.01)

        assert not saw_two_processing_at_once, "two precompile jobs ran concurrently — the queue did not serialize them"
        _await_all_terminal(client, "book-10")
        _await_all_terminal(client, "book-11")


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

        assert _status(client, "book-8") == {"tiers": [], "queue_position": None}

    def test_delete_removes_a_not_yet_started_job_from_the_queue(self, client, db_engine, seed_book, real_ffmpeg):
        seed_book(db_engine, book_id="book-12")
        seed_book(db_engine, book_id="book-13")
        _build(client, "book-12", speeds=[1.0])  # occupies the worker
        _build(client, "book-13", speeds=[1.0])  # sits behind it in queue

        d = client.delete("/precompiled/book-13/chapters/0", params={"voice": "af_heart"})
        assert d.status_code == 200
        assert precompiled_router.queue_position(("book-13", 0, "af_heart")) is None

        _await_all_terminal(client, "book-12")


class TestOrphanedJobReset:
    """A row stuck at ``processing`` because a previous process was killed
    (not stopped cleanly) mid-synthesis must not stay stuck forever — see
    `_reset_orphaned_jobs`'s docstring. Simulated directly (write a
    `processing` row, call the reset function) rather than by actually
    killing the test process.
    """

    def test_processing_row_with_no_owning_job_is_reset_to_pending(self, client, db_engine, seed_book):
        seed_book(db_engine, book_id="book-14")
        with Session(db_engine) as session:
            row = ChapterAudio(
                book_id="book-14", chapter=0, voice="af_heart", requested_speed=1.0,
                method="native", status="processing", phase="processing", progress=47,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.commit()
            row_id = row.id

        precompiled_router._reset_orphaned_jobs()

        with Session(db_engine) as session:
            reset = session.get(ChapterAudio, row_id)
            assert reset.status == "pending"
            assert reset.phase is None
            assert reset.progress == 0
            assert reset.error_message is None

    def test_done_and_error_rows_are_left_alone(self, client, db_engine, seed_book):
        seed_book(db_engine, book_id="book-15")
        with Session(db_engine) as session:
            done = ChapterAudio(
                book_id="book-15", chapter=0, voice="af_heart", requested_speed=1.0,
                method="native", status="done", progress=100,
                created_at=datetime.now(timezone.utc),
            )
            errored = ChapterAudio(
                book_id="book-15", chapter=0, voice="af_heart", requested_speed=1.5,
                method="atempo", status="error", error_message="boom",
                created_at=datetime.now(timezone.utc),
            )
            session.add(done)
            session.add(errored)
            session.commit()
            done_id, error_id = done.id, errored.id

        precompiled_router._reset_orphaned_jobs()

        with Session(db_engine) as session:
            assert session.get(ChapterAudio, done_id).status == "done"
            errored_row = session.get(ChapterAudio, error_id)
            assert errored_row.status == "error"
            assert errored_row.error_message == "boom"

    def test_start_queue_worker_resets_orphans_when_it_actually_starts_fresh(self, client, db_engine, seed_book):
        """`client` (conftest.py) already ran the lifespan once for this test,
        so the worker is live and start_queue_worker() below is a no-op
        (matching its own "only when a fresh worker actually starts" guard)
        — this asserts that guard, not a full-restart reset, since a real
        restart isn't reachable from inside one TestClient session."""
        seed_book(db_engine, book_id="book-16")
        with Session(db_engine) as session:
            row = ChapterAudio(
                book_id="book-16", chapter=0, voice="af_heart", requested_speed=1.0,
                method="native", status="processing", progress=10,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.commit()
            row_id = row.id

        precompiled_router.start_queue_worker()  # worker already running: no-op, row untouched

        with Session(db_engine) as session:
            assert session.get(ChapterAudio, row_id).status == "processing"
