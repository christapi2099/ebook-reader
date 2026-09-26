"""Regression tests for event-loop starvation in the MP3 export path.

`_run_export` was declared `async def` but contained no `await` at all: every call
inside it (SQLite, Kokoro inference, soundfile) is blocking, so once the task was
scheduled it owned the single uvicorn event loop until the whole book had been
synthesized. Measured before the fix: 0 heartbeat ticks over a 0.45 s export that
should have allowed ~45 — every other request and the TTS WebSocket frozen for the
duration.

The fix offloads the blocking body with `run_in_threadpool`. The synthesis logic
and the output format are deliberately unchanged, and these tests pin both halves
of that: responsiveness *and* identical results.

How "responsive" is asserted
----------------------------
No test in this file measures a duration. A stopwatch cannot express "the export
did not own the loop": it only expresses "this machine was fast enough while the
suite was running", which is why `elapsed < 0.5` passed in isolation and failed
under load. Every check below instead compares two *observed counts* — a
heartbeat's tick counter, or how far a concurrent coroutine had got — taken at
the first and the last synthesis call. If the export runs on the loop, those two
counts are equal; if it does not, they differ. That verdict is exact at any
machine speed and under any load, because the artificial synthesis work here is
gated on `threading.Event`s rather than on sleeps.

The only wall clock left is `HANG_GUARD`, and it is never compared against
anything the machine's speed can change: it exists so that a genuine deadlock
fails in ten seconds instead of hanging the suite for ever.
"""
import asyncio
import inspect
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from services import export_encoding
from services.tts_engine import TTSEngine

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import db.database as _db  # noqa: E402
from db.models import Book, MP3Export, Sentence  # noqa: E402
from routers import mp3 as mp3_router  # noqa: E402

SENTENCE_COUNT = 6
TERMINAL_STATUSES = ("done", "error")

#: A hang guard, not a performance budget. It bounds every wait below so that a
#: real deadlock (or a background task that never finishes) fails the test rather
#: than stalling the suite. Nothing asserts "and it was faster than this".
HANG_GUARD = 10.0

# Every export now carries its output options (format, bitrate, chapters,
# metadata) instead of having them implied. These tests are about the export
# *orchestration*, not about the container, so they ask for WAV: the one format
# `services.export_encoding` writes through soundfile with no external process.
# That keeps the file runnable on a machine without ffmpeg, which
# ``_no_ffmpeg_dependency`` below enforces rather than assumes.
WAV_OPTIONS = mp3_router.ExportOptions(format="wav", bitrate_kbps=None)


@pytest.fixture(autouse=True)
def _no_ffmpeg_dependency(monkeypatch):
    """Make `ffmpeg` on PATH irrelevant here, and say so loudly if it comes back.

    Nothing in this file is about encoding containers, so the encoder probe is
    pinned off: on a machine without ffmpeg these exports behave exactly as they
    do on a machine with it, instead of the POST returning 503. The pin is an
    assertion of intent as much as a stub — if a future change routes one of
    these exports through ffmpeg, the export fails here immediately instead of
    quietly depending on the developer's PATH.
    """
    monkeypatch.setattr(export_encoding, "ffmpeg_available", lambda: False)


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
    """A pending export row for a 6-sentence book, with all IO redirected to tmp.

    The row carries the same output options every test in this file passes to
    ``_run_export``, because ``create_export`` is what normally records them: a
    row built here for a different format than the one rendered would let the
    export label its file wrongly and no assertion would notice.
    """
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
                           progress=0, format=WAV_OPTIONS.format,
                           bitrate_kbps=WAV_OPTIONS.bitrate_kbps,
                           created_at=datetime.now(timezone.utc))
        s.add(export)
        s.commit()
        s.refresh(export)
        export_id = export.id

    yield export_id

    # Drain before dropping the registry. ``clear()`` on its own does not stop a
    # running export, it only hides it: the task keeps writing, and the next test
    # file's own drain then sees an empty registry and proceeds. Fail loudly
    # instead, so a leak names the test that caused it rather than surfacing as a
    # corrupted assertion one file later.
    _drain_exports()
    still_running = [t for t in mp3_router._export_tasks.values() if not t.done()]
    assert not still_running, (
        f"{len(still_running)} export task(s) outlived their test; call "
        "_drain_exports() inside the TestClient context before it closes"
    )
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


def _await_status(client: TestClient, export_id: int, timeout: float = HANG_GUARD) -> str:
    """Poll the status endpoint until the export reaches a terminal state.

    A wait, not an assertion about speed: the alternative to polling is an event
    the running server does not publish, so it is bounded by ``HANG_GUARD`` and
    the caller asserts the value it got.
    """
    deadline = time.monotonic() + timeout
    status = "unknown"
    while time.monotonic() < deadline:
        status = client.get(f"/mp3/exports/{export_id}/status").json()["status"]
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(0.02)
    return status


def _install_pipeline(monkeypatch, kokoro):
    """Install ``kokoro`` as both the router's pipeline and its engine's.

    The export path synthesises through a TTSEngine so it shares the
    speed-capability probe and the AudioCache with playback. Installing the stub
    therefore means installing the engine too: reaching past it by setting only
    `_kokoro` would leave the engine unset and the export would produce nothing.
    """
    monkeypatch.setattr(mp3_router, "_kokoro", kokoro)
    monkeypatch.setattr(mp3_router, "_engine", TTSEngine(kokoro))
    return kokoro


def _install_kokoro(monkeypatch, *, delay=0.0, calls=None, threads=None, samples=2400):
    def kokoro(text, voice=None, speed=None):
        if calls is not None:
            calls.append(text)
        if threads is not None:
            threads.append(threading.get_ident())
        if delay:
            time.sleep(delay)
        return [(None, None, np.ones(samples, dtype=np.float32))]

    return _install_pipeline(monkeypatch, kokoro)


class _GatedSynthesis:
    """A fake Kokoro that parks every sentence until the test opens the gate.

    A sleep would only make "synthesis is still running" *likely*, and how likely
    would depend on the machine. A gate makes it a fact, which is what lets
    `test_post_export_returns_before_the_export_finishes` assert an ordering
    instead of measuring a duration.
    """

    def __init__(self, sentences: int = SENTENCE_COUNT, samples: int = 2400):
        self.sentences = sentences
        self.samples = samples
        self.texts: list[str] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def __call__(self, text, voice=None, speed=None):
        self.texts.append(text)
        self.entered.set()
        if not self.release.wait(HANG_GUARD):
            raise AssertionError(
                f"synthesis was still parked after {HANG_GUARD}s: the export "
                "never got a chance to run"
            )
        if len(self.texts) >= self.sentences:
            self.finished.set()
        return [(None, None, np.ones(self.samples, dtype=np.float32))]


# ---------------------------------------------------------------------------
# The contract of the refactor itself
# ---------------------------------------------------------------------------

def test_blocking_body_is_synchronous_and_the_entry_point_is_async():
    assert inspect.iscoroutinefunction(mp3_router._run_export)
    assert not inspect.iscoroutinefunction(mp3_router._run_export_blocking)


def test_synthesis_runs_off_the_event_loop_thread(seeded, monkeypatch):
    """Deterministic proof of the fix: no timing thresholds involved.

    The thread that runs the *export body* is what the fix moved, and that is
    what gets recorded here. Watching only kokoro's own thread is not enough:
    ``_synthesize`` hands the call to ``services.tts_engine``'s module-level
    synthesis pool, so kokoro runs off the loop thread even when the export body
    does not — which is why this test passed with the pre-fix code reinstated.
    """
    synthesis_threads: list[int] = []
    body_threads: list[int] = []
    loop_thread_holder: dict[str, int] = {}

    real_body = mp3_router._run_export_blocking

    def recording_body(*args, **kwargs):
        body_threads.append(threading.get_ident())
        return real_body(*args, **kwargs)

    monkeypatch.setattr(mp3_router, "_run_export_blocking", recording_body)
    _install_kokoro(monkeypatch, threads=synthesis_threads)

    async def scenario():
        loop_thread_holder["id"] = threading.get_ident()
        await mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS)

    asyncio.run(scenario())

    assert body_threads, "the export body never ran"
    assert synthesis_threads, "kokoro was never called"
    loop_thread = loop_thread_holder["id"]
    assert loop_thread == threading.main_thread().ident
    assert all(tid != loop_thread for tid in body_threads), (
        "the export body ran on the event loop thread — the export still blocks "
        "the loop for as long as it takes"
    )
    assert all(tid != loop_thread for tid in synthesis_threads), (
        "synthesis ran on the event loop thread"
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
    """The loop must keep turning *while* a sentence is being synthesised.

    Causal, not a speed ratio. The old version asserted
    ``ticks >= elapsed / 0.005 * 0.4``, which is a statement about how much CPU
    the machine had to spare. The property worth proving is an ordering: if the
    export owns the loop, the heartbeat cannot run at all between two synthesis
    calls. So the tick counter is sampled from inside every synthesis call and the
    first sample is compared with the last — a comparison of two counters, exact
    at any machine speed.
    """
    ticks = {"n": 0}
    ticks_at_call: list[int] = []

    def kokoro(text, voice=None, speed=None):
        ticks_at_call.append(ticks["n"])
        time.sleep(0.05)  # stands in for inference, on whatever thread runs it
        return [(None, None, np.ones(2400, dtype=np.float32))]

    _install_pipeline(monkeypatch, kokoro)

    async def scenario():
        async def heartbeat():
            while True:
                ticks["n"] += 1
                await asyncio.sleep(0.005)

        hb = asyncio.create_task(heartbeat())
        try:
            await asyncio.wait_for(
                mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS),
                timeout=HANG_GUARD,
            )
        finally:
            hb.cancel()

    asyncio.run(scenario())

    assert len(ticks_at_call) == SENTENCE_COUNT, (
        f"expected one synthesis per sentence, saw {len(ticks_at_call)}"
    )
    assert ticks_at_call[-1] > ticks_at_call[0], (
        "the heartbeat never got a turn between the first and the last synthesis "
        f"call: the tick counter stayed at {ticks_at_call[0]} across all "
        f"{len(ticks_at_call)} calls, so the export owned the event loop"
    )


def test_a_concurrent_request_is_served_while_synthesis_is_running(seeded, monkeypatch):
    """The user-visible symptom: everything else stalled for the whole export.

    The old version only asserted that the concurrent coroutine finished
    *eventually* (``len(served) == 10`` after ``await companion``), which it did
    even when it had been blocked for the entire export and only ran afterwards —
    so that assertion passed with the bug present. What is asserted now is that
    the coroutine got *further* between the first and the last synthesis call,
    which cannot happen while the export owns the loop.
    """
    served: list[int] = []
    served_at_call: list[int] = []

    def kokoro(text, voice=None, speed=None):
        served_at_call.append(len(served))
        time.sleep(0.05)
        return [(None, None, np.ones(2400, dtype=np.float32))]

    _install_pipeline(monkeypatch, kokoro)

    async def scenario():
        async def other_request():
            for _ in range(10):
                await asyncio.sleep(0.01)
                served.append(1)

        companion = asyncio.create_task(other_request())
        await asyncio.wait_for(
            mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS),
            timeout=HANG_GUARD,
        )
        await companion

    asyncio.run(scenario())

    assert len(served) == 10, "a concurrent coroutine could not make progress"
    assert served_at_call[0] < 10, (
        "the concurrent coroutine finished before the first synthesis call, so "
        "this run cannot observe the overlap it is meant to test"
    )
    assert served_at_call[-1] > served_at_call[0], (
        "the concurrent coroutine made no progress between the first and the last "
        f"synthesis call (stuck at {served_at_call[0]} of 10), so the export "
        "owned the event loop"
    )


# ---------------------------------------------------------------------------
# Behaviour must be unchanged
# ---------------------------------------------------------------------------

def test_export_still_completes_with_identical_output(seeded, monkeypatch, engine):
    """The refactor moved the thread, not the arithmetic.

    WAV is what makes this claim checkable without an external encoder: the file
    can be decoded and compared with what the fake pipeline produced, so the
    assertion is about the audio rather than about "some bytes were written".
    Every sentence contributes its 2400 samples of 1.0, concatenated in order.
    """
    import soundfile as sf

    calls: list[str] = []
    _install_kokoro(monkeypatch, calls=calls)

    asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS))

    with Session(engine) as s:
        export = s.get(MP3Export, seeded)
        assert export.status == "done"
        assert export.progress == 100
        assert export.file_size and export.file_size > 0
        assert export.format == "wav", "the export must record the container it wrote"
        written = Path(export.file_path)
        assert written.exists() and written.stat().st_size == export.file_size
        assert written.suffix == ".wav"

    decoded, rate = sf.read(str(written), dtype="float32")
    assert rate == 24000
    assert decoded.size == SENTENCE_COUNT * 2400, (
        "the written file does not hold one buffer per sentence"
    )
    assert np.allclose(decoded, 1.0, atol=1e-3), (
        "the audio in the file is not the audio the pipeline produced"
    )

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
    asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS))

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
        asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS))
    finally:
        mp3_router._synthesize = original

    assert seen[-1] > 0, "progress was never persisted mid-export"


def test_no_audio_marks_the_export_as_error(seeded, engine, monkeypatch):
    # Both globals, and through monkeypatch. Setting only `_kokoro` leaves a
    # previously-installed `_engine` in place, so the export would still succeed
    # and this test's result would depend on whatever ran before it.
    monkeypatch.setattr(mp3_router, "_kokoro", None)
    monkeypatch.setattr(mp3_router, "_engine", None)

    asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS))

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
    asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS))

    with Session(engine) as s:
        assert s.get(MP3Export, seeded).status == "error"


def test_missing_book_marks_the_export_as_error(seeded, monkeypatch, engine):
    _install_kokoro(monkeypatch)
    asyncio.run(mp3_router._run_export(seeded, "does-not-exist", "af_heart", 1.0, WAV_OPTIONS))
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
        task = asyncio.create_task(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS))
        mp3_router._export_tasks[seeded] = task
        await task

    asyncio.run(scenario())
    assert seeded not in mp3_router._export_tasks


# ---------------------------------------------------------------------------
# HTTP surface: the POST returns while the export is still running
# ---------------------------------------------------------------------------

def test_post_export_returns_before_the_export_finishes(seeded, monkeypatch):
    """POST /mp3/export must hand back a *pending* export, not wait for it.

    No stopwatch. Synthesis is parked on a gate that only this test can open, so
    "the export had not finished when the POST returned" is a fact about ordering
    rather than about how fast the machine is.

    The status request is what makes the assertion bite. Under the pre-fix code
    the export body ran on the event loop thread, so the loop could not answer
    ``GET /mp3/exports/{id}/status`` until the export was over: the status read
    came back ``done`` (or ``error``), which is exactly the failure reported
    below. With the body on a worker thread the read is answered while synthesis
    is still parked.
    """
    gated = _GatedSynthesis()
    _install_pipeline(monkeypatch, gated)

    app = FastAPI()
    app.include_router(mp3_router.router)
    returned = threading.Event()
    posted: dict[str, Any] = {}

    with TestClient(app) as client:

        def post():
            try:
                posted["response"] = client.post(
                    "/mp3/export",
                    json={
                        "book_id": "bk",
                        "voice": "af_heart",
                        "speed": 1.0,
                        "format": "wav",
                    },
                )
            finally:
                returned.set()

        poster = threading.Thread(target=post, daemon=True)
        poster.start()

        assert gated.entered.wait(HANG_GUARD), "the export never started synthesising"
        assert returned.wait(HANG_GUARD), (
            "POST /mp3/export had not returned while synthesis was still parked, "
            "so the request is being served by the thread the export runs on"
        )

        response = posted["response"]
        assert response.status_code == 200, response.text
        export_id = response.json()["export_id"]

        # Nothing has left the gate yet (it is opened below), so if the status
        # endpoint reports a terminal state, the POST itself waited for the
        # export rather than starting it in the background.
        status = client.get(f"/mp3/exports/{export_id}/status").json()["status"]
        assert status not in TERMINAL_STATUSES, (
            f"the export was already {status!r} when the POST returned — the POST "
            "waited for the export instead of starting it in the background"
        )

        gated.release.set()
        poster.join(HANG_GUARD)
        assert not poster.is_alive(), "the POST never returned after the gate opened"

        # And the background task still finishes.
        final = _await_status(client, export_id)
        assert final == "done", f"export never completed, last status={final!r}"

        # Terminal *status* is not the same as a finished *task*: the worker
        # commits "done" and only then unwinds. Leaving at that point closes the
        # TestClient on a worker still mid-transaction, and because the in-memory
        # database is one shared connection, the abandoned session can roll back
        # the next test file's inserts -- measured as a vanished Book row and
        # therefore a book title of "Unknown". Wait for the task, not the status.
        _drain_exports()

    assert gated.texts == [f"Sentence number {i}." for i in range(SENTENCE_COUNT)]


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

        asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.5, WAV_OPTIONS))

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

        asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.5, WAV_OPTIONS))

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

        asyncio.run(mp3_router._run_export(seeded, "bk", "af_heart", 1.0, WAV_OPTIONS))
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

        asyncio.run(mp3_router._run_export(second_id, "bk", "af_heart", 1.0, WAV_OPTIONS))

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
            response = client.post("/mp3/export", json={"book_id": "bk", "speed": bad, "format": "wav"})
        assert response.status_code == 400
        assert response.json()["detail"]

    def test_out_of_band_speeds_are_clamped_not_rejected(self, seeded, engine, monkeypatch):
        with self._client(monkeypatch) as client:
            response = client.post("/mp3/export", json={"book_id": "bk", "speed": 99, "format": "wav"})
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
