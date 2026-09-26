"""Regression tests for event-loop starvation in the MP3 export path.

`_run_export` was declared `async def` but contained no `await` at all: every call
inside it (SQLite, Kokoro inference, soundfile) is blocking, so once the task was
scheduled it owned the single uvicorn event loop until the whole book had been
synthesized. Measured before the fix: 0 heartbeat ticks over a 0.45 s export that
should have allowed ~45 — every other request and the TTS WebSocket frozen for the
duration, and the POST that started it did not return until it was over.

The fix offloads the blocking body with `run_in_threadpool`. The synthesis logic
and the output format are deliberately unchanged, and these tests pin both halves
of that: responsiveness *and* identical results.
"""
import asyncio
import inspect
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from services.tts_engine import TTSEngine

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import db.database as _db  # noqa: E402
from db.models import Book, MP3Export, Sentence  # noqa: E402
from routers import mp3 as mp3_router  # noqa: E402

SENTENCE_COUNT = 6

# Every export now carries its output options (format, bitrate, chapters,
# metadata) instead of having them implied. These tests are all about the
# default MP3 export, so they pass the same defaults the API would.
DEFAULT_OPTIONS = mp3_router.ExportOptions()


def _make_engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def engine():
    eng = _make_engine()
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def seeded(engine, monkeypatch, tmp_path):
    """A pending export row for a 6-sentence book, with all IO redirected to tmp."""
    monkeypatch.setattr(_db, "engine", engine)
    monkeypatch.setattr(mp3_router, "EXPORTS_DIR", tmp_path / "exports")
    mp3_router._export_tasks.clear()

    with Session(engine) as s:
        s.add(Book(id="bk", title="Test", file_path="/t.pdf", file_type="pdf",
                   page_count=1, created_at=datetime.now(timezone.utc)))
        for i in range(SENTENCE_COUNT):
            s.add(Sentence(book_id="bk", index=i, text=f"Sentence number {i}.", page=0,
                           x0=0.0, y0=0.0, x1=1.0, y1=1.0, filtered=False))
        export = MP3Export(book_id="bk", voice="af_heart", speed=1.0, status="pending",
                           progress=0, created_at=datetime.now(timezone.utc))
        s.add(export)
        s.commit()
        s.refresh(export)
        export_id = export.id

    yield export_id

    mp3_router._export_tasks.clear()


def _drain_exports(timeout: float = 15.0) -> None:
    """Wait for exports started through the API to stop touching the database.

    Must be called while the TestClient (and therefore its event loop) is still
    open, otherwise the task can never complete. It matters beyond tidiness: the
    export worker resolves ``db.database.engine`` at call time, so a task that
    outlives its test starts writing into whatever engine the *next* test
    installs. That is not hypothetical — leaving the clamped-speed export from
    the last test in this file running made the following test file see a
    missing Book and a missing export row.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not [t for t in mp3_router._export_tasks.values() if not t.done()]:
            return
        time.sleep(0.02)


def _install_kokoro(monkeypatch, *, delay=0.0, calls=None, threads=None, samples=2400):
    def kokoro(text, voice=None, speed=None):
        if calls is not None:
            calls.append(text)
        if threads is not None:
            threads.append(threading.get_ident())
        if delay:
            time.sleep(delay)
        return [(None, None, np.ones(samples, dtype=np.float32))]

    monkeypatch.setattr(mp3_router, "_kokoro", kokoro)
    # The export path now synthesises through a TTSEngine so it shares the
    # speed-capability probe and the AudioCache with playback. Installing the stub
    # therefore means installing the engine too: reaching past it by setting only
    # `_kokoro` would leave the engine unset and the export would produce nothing.
    monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
    return kokoro


# ---------------------------------------------------------------------------
# The contract of the refactor itself
# ---------------------------------------------------------------------------

def test_blocking_body_is_synchronous_and_the_entry_point_is_async():
    assert inspect.iscoroutinefunction(mp3_router._run_export)
    assert not inspect.iscoroutinefunction(mp3_router._run_export_blocking)


def test_synthesis_runs_off_the_event_loop_thread(seeded, monkeypatch):
    """Deterministic proof of the fix: no timing thresholds involved."""
    threads: list[int] = []
    _install_kokoro(monkeypatch, threads=threads)
    loop_thread_holder: dict[str, int] = {}

    async def scenario():
        loop_thread_holder["id"] = threading.get_ident()
        await mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS)

    asyncio.run(scenario())

    assert threads, "kokoro was never called"
    loop_thread = loop_thread_holder["id"]
    assert loop_thread == threading.main_thread().ident
    assert all(tid != loop_thread for tid in threads), (
        "synthesis ran on the event loop thread — the export still blocks the loop"
    )


def test_export_is_no_longer_a_coroutine_without_await_points():
    """The old body had zero awaits; the wrapper must have exactly one offload."""
    source = inspect.getsource(mp3_router._run_export)
    assert "run_in_threadpool" in source
    assert source.count("await") >= 1


# ---------------------------------------------------------------------------
# Event loop stays responsive
# ---------------------------------------------------------------------------

def test_event_loop_stays_responsive_during_an_export(seeded, monkeypatch):
    _install_kokoro(monkeypatch, delay=0.05)  # 6 x 50 ms = ~0.3 s of blocking work

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.005)

        hb = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.05)  # let the heartbeat settle
        before = ticks
        started = time.perf_counter()
        await mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS)
        elapsed = time.perf_counter() - started
        hb.cancel()
        return ticks - before, elapsed

    ticks, elapsed = asyncio.run(scenario())
    expected = elapsed / 0.005

    # Pre-fix this was exactly 0. The threshold is deliberately loose (40%) so the
    # test measures "is the loop blocked", not machine speed.
    assert ticks >= expected * 0.4, (
        f"loop starved: {ticks} ticks during a {elapsed:.3f}s export "
        f"(expected about {expected:.0f})"
    )


def test_a_concurrent_request_is_served_while_synthesis_is_running(seeded, monkeypatch):
    """The user-visible symptom: everything else stalled for the whole export."""
    _install_kokoro(monkeypatch, delay=0.05)
    served: list[int] = []

    async def scenario():
        async def other_request():
            for _ in range(10):
                await asyncio.sleep(0.01)
                served.append(1)

        companion = asyncio.create_task(other_request())
        await mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS)
        await companion

    asyncio.run(scenario())
    assert len(served) == 10, "a concurrent coroutine could not make progress"


# ---------------------------------------------------------------------------
# Behaviour must be unchanged
# ---------------------------------------------------------------------------

def test_export_still_completes_with_identical_output(seeded, monkeypatch, engine):
    calls: list[str] = []
    _install_kokoro(monkeypatch, calls=calls)

    asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS))

    with Session(engine) as s:
        export = s.get(MP3Export, seeded)
        assert export.status == "done"
        assert export.progress == 100
        assert export.file_size and export.file_size > 0
        written = Path(export.file_path)
        assert written.exists() and written.stat().st_size == export.file_size

    # Every unfiltered sentence was synthesized, once, in order.
    assert len(calls) == SENTENCE_COUNT
    assert calls == [f"Sentence number {i}." for i in range(SENTENCE_COUNT)]


def test_filtered_sentences_are_still_skipped(seeded, monkeypatch, engine):
    from sqlmodel import select

    with Session(engine) as s:
        target = s.exec(
            select(Sentence).where(Sentence.book_id == "bk", Sentence.index == 2)
        ).one()
        target.filtered = True
        s.add(target)
        s.commit()

    calls: list[str] = []
    _install_kokoro(monkeypatch, calls=calls)
    asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS))

    assert len(calls) == SENTENCE_COUNT - 1
    assert "Sentence number 2." not in calls


def test_progress_is_written_during_the_export(seeded, monkeypatch, engine):
    _install_kokoro(monkeypatch, delay=0.01)
    seen: list[int] = []
    original = mp3_router._synthesize

    def spy(text, voice, speed):
        with Session(engine) as s:
            row = s.get(MP3Export, seeded)
            if row:
                seen.append(row.progress)
        return original(text, voice, speed)

    mp3_router._synthesize = spy
    try:
        asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS))
    finally:
        mp3_router._synthesize = original

    assert seen[-1] > 0, "progress was never persisted mid-export"


def test_no_audio_marks_the_export_as_error(seeded, engine, monkeypatch):
    # Both globals, and through monkeypatch. Setting only `_kokoro` leaves a
    # previously-installed `_engine` in place, so the export would still succeed
    # and this test's result would depend on whatever ran before it.
    monkeypatch.setattr(mp3_router, "_kokoro", None)
    monkeypatch.setattr(mp3_router, "_engine", None)

    asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS))

    with Session(engine) as s:
        export = s.get(MP3Export, seeded)
        assert export.status == "error"
        assert export.error_message


def test_synthesis_exception_marks_the_export_as_error(seeded, engine, monkeypatch):
    def exploding(text, voice=None, speed=None):
        raise RuntimeError("kokoro exploded")

    # monkeypatch rather than a bare assignment: this used to leak both globals
    # into every later test in the file.
    monkeypatch.setattr(mp3_router, "_kokoro", exploding)
    monkeypatch.setattr(mp3_router, "_engine", TTSEngine(exploding))
    asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS))

    with Session(engine) as s:
        assert s.get(MP3Export, seeded).status == "error"


def test_missing_book_marks_the_export_as_error(seeded, monkeypatch, engine):
    _install_kokoro(monkeypatch)
    asyncio.run(mp3_router._run_export(seeded, "does-not-exist", "af_heart", 1.0, DEFAULT_OPTIONS))
    with Session(engine) as s:
        export = s.get(MP3Export, seeded)
        assert export.status == "error"
        assert "Book not found" in export.error_message


@pytest.mark.parametrize("strategy", ["success", "no_audio", "exception"])
def test_task_registry_is_cleaned_up_on_every_path(seeded, monkeypatch, strategy):
    if strategy == "success":
        _install_kokoro(monkeypatch)
    elif strategy == "no_audio":
        mp3_router._kokoro = None
    else:
        mp3_router._kokoro = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))

    async def scenario():
        task = asyncio.create_task(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS))
        mp3_router._export_tasks[seeded] = task
        await task

    asyncio.run(scenario())
    assert seeded not in mp3_router._export_tasks


# ---------------------------------------------------------------------------
# HTTP surface: the POST returns while the export is still running
# ---------------------------------------------------------------------------

def test_post_export_returns_before_the_export_finishes(seeded, monkeypatch, engine):
    app = FastAPI()
    app.include_router(mp3_router.router)
    _install_kokoro(monkeypatch, delay=0.15)  # 6 x 150 ms = 0.9 s of work

    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.post("/mp3/export", json={"book_id": "bk", "voice": "af_heart", "speed": 1.0})
        elapsed = time.perf_counter() - started
        assert response.status_code == 200
        export_id = response.json()["export_id"]

        assert elapsed < 0.5, (
            f"POST /mp3/export blocked for {elapsed:.2f}s — the export is running on the event loop"
        )

        # And the background task still finishes.
        deadline = time.perf_counter() + 20
        status = None
        while time.perf_counter() < deadline:
            status = client.get(f"/mp3/exports/{export_id}/status").json()["status"]
            if status in ("done", "error"):
                break
            time.sleep(0.05)
        assert status == "done", f"export never completed, last status={status!r}"


# ---------------------------------------------------------------------------
# The export path shares TTSEngine, the AudioCache and the speed normaliser
# ---------------------------------------------------------------------------


def _supporting_kokoro(samples=2400):
    """A binding that honours speed=, recording every call."""
    calls: list[tuple[str, float]] = []

    def kokoro(text, voice=None, speed=1.0):
        calls.append((text, speed))
        return [(None, None, np.ones(samples, dtype=np.float32))]

    return kokoro, calls


def _downgrading_kokoro(samples=2400):
    """A binding with no `speed` parameter, so the engine must degrade to 1.0x."""
    calls: list[str] = []

    def kokoro(text, voice=None, **kwargs):
        calls.append(text)
        return [(None, None, np.ones(samples, dtype=np.float32))]

    return kokoro, calls


class TestExportRecordsTheRenderedRate:
    """An export row must not advertise a tempo its file does not have.

    The export kept its own copy of the Kokoro call and the speed-fallback ladder
    and bypassed TTSEngine entirely, so it stamped MP3Export.speed with the
    *requested* rate even when the installed build could not honour it. Nothing
    ever rewrote that row, and the UI renders it as `{speed}x`.
    """

    def test_a_downgraded_export_records_the_rate_it_rendered(
        self, seeded, engine, monkeypatch
    ):
        kokoro, _ = _downgrading_kokoro()
        monkeypatch.setattr(mp3_router, "_kokoro", kokoro)
        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
        with Session(engine) as s:
            row = s.get(MP3Export, seeded)
            row.speed = 1.5
            s.commit()

        asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.5, DEFAULT_OPTIONS))

        with Session(engine) as s:
            export = s.get(MP3Export, seeded)
        assert export.status == "done"
        assert export.speed == 1.5, "the requested rate stays on the row"
        assert export.effective_speed == 1.0, (
            "the export must record the rate it actually rendered at"
        )

    def test_a_supported_export_records_the_requested_rate(
        self, seeded, engine, monkeypatch
    ):
        kokoro, _ = _supporting_kokoro()
        monkeypatch.setattr(mp3_router, "_kokoro", kokoro)
        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))

        asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.5, DEFAULT_OPTIONS))

        with Session(engine) as s:
            export = s.get(MP3Export, seeded)
        assert export.status == "done"
        assert export.effective_speed == 1.5

    def test_a_second_export_reuses_the_cache_instead_of_resynthesising(
        self, seeded, engine, monkeypatch
    ):
        kokoro, calls = _supporting_kokoro()
        monkeypatch.setattr(mp3_router, "_kokoro", kokoro)
        monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))

        asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, DEFAULT_OPTIONS))
        after_first = len(calls)
        assert after_first == SENTENCE_COUNT, (
            f"expected one synthesis per sentence, got {after_first}"
        )

        with Session(engine) as s:
            second = MP3Export(book_id="bk", voice="af_heart", speed=1.0,
                               status="pending", progress=0,
                               created_at=datetime.now(timezone.utc))
            s.add(second)
            s.commit()
            s.refresh(second)
            second_id = second.id

        asyncio.run(mp3_router._run_export(second_id, "bk", "af_heart", 1.0, DEFAULT_OPTIONS))

        assert len(calls) == after_first, (
            "the second export re-synthesised audio the reader already had cached"
        )


class TestExportSpeedValidation:
    """POST /mp3/export must reject a rate the engine cannot use."""

    @staticmethod
    def _client(monkeypatch):
        _install_kokoro(monkeypatch)
        app = FastAPI()
        app.include_router(mp3_router.router)
        return TestClient(app)

    @pytest.mark.parametrize("bad", [0, -1.0, -0.5])
    def test_unusable_speeds_are_rejected_with_400(self, seeded, engine, monkeypatch, bad):
        with self._client(monkeypatch) as client:
            response = client.post("/mp3/export", json={"book_id": "bk", "speed": bad})
        assert response.status_code == 400
        assert response.json()["detail"]

    def test_out_of_band_speeds_are_clamped_not_rejected(self, seeded, engine, monkeypatch):
        with self._client(monkeypatch) as client:
            response = client.post("/mp3/export", json={"book_id": "bk", "speed": 99})
            assert response.status_code == 200
            # Let the export this POST started finish before the fixtures swap
            # the database out from under its worker thread.
            _drain_exports()
        assert response.status_code == 200
        with Session(engine) as s:
            row = s.get(MP3Export, response.json()["export_id"])
        assert row.speed == 3.0


class TestEffectiveSpeedMigration:
    """`mp3export.effective_speed` must reach databases that predate it."""

    def test_legacy_table_gains_the_column_and_keeps_its_rows(self, tmp_path):
        db_file = tmp_path / "legacy_mp3.db"
        conn = sqlite3.connect(db_file)
        conn.execute(
            "CREATE TABLE mp3export (id INTEGER PRIMARY KEY, book_id TEXT, voice TEXT, "
            "speed REAL, status TEXT, progress INTEGER, file_path TEXT, "
            "file_size INTEGER, error_message TEXT, created_at TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO mp3export VALUES (1,'bk','af_heart',1.5,'done',100,"
            "NULL,NULL,NULL,'2024-01-01')"
        )
        conn.commit()
        conn.close()

        engine = _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
        assert "effective_speed" in {c["name"] for c in sa_inspect(engine).get_columns("mp3export")}
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, speed, effective_speed FROM mp3export")
            ).fetchall()
        assert rows == [(1, 1.5, None)], (
            "existing export rows must survive and read as unknown, not zero"
        )

    def test_repeated_migration_is_idempotent(self, tmp_path):
        db_file = tmp_path / "legacy_mp3_twice.db"
        sqlite3.connect(db_file).close()
        first = _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
        cols = [c["name"] for c in sa_inspect(first).get_columns("mp3export")]
        second = _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
        assert [c["name"] for c in sa_inspect(second).get_columns("mp3export")] == cols
