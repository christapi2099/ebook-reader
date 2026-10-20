"""Process-first render worker: pipeline, resume, supersede/cancel/delete, failures."""
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.database as _db  # noqa: E402
import storage  # noqa: E402
from db.models import AudioCache, Book, Sentence  # noqa: E402
from services import export_encoding, render_artifacts as ra, render_jobs as rj  # noqa: E402
from services import render_worker as rw  # noqa: E402

SR = 24000
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
TIERS = (1.5,)  # one tier keeps the real-ffmpeg tests fast; the pipeline is tier-agnostic


# ------------------------------------------------------------------- fakes
class FakeSynth:
    """Deterministic 220 Hz tone, 20 ms per character, plus a short marker burst
    at the start whose amplitude encodes the text length. Counts calls per text."""

    def __init__(self, hook=None, effective_speed=1.0):
        self.calls: list[str] = []
        self.hook = hook
        self.effective_speed = effective_speed

    def __call__(self, text, voice):
        self.calls.append(text)
        if self.hook:
            self.hook(len(self.calls), text)
        n = int(SR * 0.02 * len(text))
        t = np.arange(n) / SR
        a = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        a[:240] *= 0.2 + (len(text) % 5) * 0.1
        return rw.SynthResult(a, self.effective_speed)


class FakeAligner:
    def align(self, audio, sr, words):
        dur = len(audio) / sr * 1000
        step = dur / len(words)
        return [(i * step, (i + 1) * step, 0.9) for i in range(len(words))]


def ffmpeg_required():
    assert export_encoding.ffmpeg_available(), "ffmpeg is required for this test"


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def env(db_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "engine", db_engine)
    monkeypatch.setattr(storage, "RENDERS_DIR", tmp_path / "renders")
    (tmp_path / "renders").mkdir()
    return db_engine


def texts(n=6):
    return [f"Sentence number {i} is read aloud here." for i in range(n)]


def mk_book(eng, bid="b1", n=6, target="fpA", filtered=(), **kw):
    kw.setdefault("job_status", "pending")
    with Session(eng) as s:
        s.add(Book(id=bid, title=bid, file_path="missing.pdf", file_type="text", page_count=1,
                   created_at=T0, target_fingerprint=target, **kw))
        s.add_all(Sentence(book_id=bid, index=i, text=t, page=0, x0=0, y0=0, x1=0, y1=0,
                           filtered=i in filtered, chapter=0 if i < 3 else 1)
                 for i, t in enumerate(texts(n)))
        s.commit()


def book(eng, bid="b1"):
    with Session(eng) as s:
        b = s.get(Book, bid)
        if b is not None:
            s.expunge(b)
        return b


def sentence_rows(eng, bid="b1"):
    with Session(eng) as s:
        return [(r.index, r.text, r.filtered, r.chapter) for r in s.exec(
            select(Sentence).where(Sentence.book_id == bid).order_by(Sentence.index))]


def run(synth, bid="b1", worker="w1", **kw):
    kw.setdefault("aligner", FakeAligner())
    kw.setdefault("tiers", TIERS)
    got = rj.claim_next(worker, T0 + timedelta(days=1), debounce_s=0)
    assert got == bid
    return rw.render_book(bid, worker, synthesizer=synth, **kw)


def gen_files(bid, gen):
    d = storage.RENDERS_DIR / bid / str(gen)
    return {str(p.relative_to(d)): p.read_bytes() for p in d.rglob("*") if p.is_file()}


def tmp_dirs(bid="b1"):
    return list((storage.RENDERS_DIR / bid).glob(".tmp-*"))


# ---------------------------------------------------------- state machine
def test_upload_pending_processing_ready(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env, filtered=(4,))
    assert book(env).job_status == "pending" and book(env).ready_generation is None
    seen = []
    synth = FakeSynth(hook=lambda n, t: seen.append(book(env).job_status))
    assert run(synth) == rw.READY
    assert set(seen) == {"processing"}
    b = book(env)
    assert (b.job_status, b.ready_generation, b.ready_fingerprint) == ("idle", 1, "fpA")
    assert b.lease_owner is None and b.progress == 1.0
    files = gen_files("b1", 1)
    assert {"base.flac", "audio_1.0.opus", "audio_1.5.opus", "manifest.json",
            "remap_from_0.json"} <= set(files)
    assert not tmp_dirs()
    # resume state is dropped once published
    assert not any(p.startswith(("chunks/", "checkpoint.json")) for p in files)
    assert len(synth.calls) == 5  # the filtered sentence is never synthesized
    assert len(sentence_rows(env)) == 6  # ...but it is kept as a row
    m = json.loads(files["manifest.json"])
    assert m["fingerprint"] == "fpA" and m["mixed_backends"] is False
    segs = m["tiers"]["1.0"]["segments"]
    assert [s["index"] for s in segs] == [0, 1, 2, 3, 5]
    assert all(s["words"] for s in segs)  # every sentence has word entries
    for a, b2 in zip(segs, segs[1:]):
        assert b2["start_ms"] >= a["end_ms"] + a["gap_ms"] - 1e-6
    assert segs[2]["gap_ms"] == ra.GAP_CHAPTER_MS  # chapter boundary after index 2


def test_error_then_retry_then_ready_resumes(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env)
    boom = {"on": True}

    def hook(n, t):
        if boom["on"] and n == 4:
            boom["on"] = False
            raise RuntimeError("synth exploded")

    synth = FakeSynth(hook=hook)
    assert run(synth) == rw.ERROR
    b = book(env)
    assert b.job_status == "error" and "synth exploded" in b.error_message
    assert "Traceback" in b.error_message and b.ready_generation is None
    assert rj.request_render("b1", "fpA", debounce=False) == "pending"
    assert run(synth, worker="w2") == rw.READY
    assert book(env).ready_generation == 1
    # sentences 0-2 finished before the failure: only the failing one ran twice
    assert sorted(synth.calls) == sorted(texts() + [texts()[3]])


def test_rerender_keeps_old_generation_readable_throughout(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env)
    assert run(FakeSynth()) == rw.READY
    old_files = gen_files("b1", 1)
    assert rj.request_render("b1", "fpB", debounce=False) == "pending"
    seen = []

    def hook(n, t):
        b = book(env)
        seen.append((b.ready_generation, b.ready_fingerprint, (storage.RENDERS_DIR / "b1/1").is_dir()))

    assert run(FakeSynth(hook=hook), worker="w2") == rw.READY
    assert set(seen) == {(1, "fpA", True)}
    b = book(env)
    assert (b.ready_generation, b.ready_fingerprint) == (2, "fpB")
    assert gen_files("b1", 1) == old_files  # old generation untouched (GC removes it later)


# --------------------------------------------------------------- atomicity
@pytest.mark.parametrize("where", ["after_n_sentences", "after_encode", "after_rename"])
def test_injected_failures_leave_old_generation_intact(env, real_ffmpeg, where):
    ffmpeg_required()
    mk_book(env)
    assert run(FakeSynth()) == rw.READY
    old_files, old_rows = gen_files("b1", 1), sentence_rows(env)
    rj.request_render("b1", "fpB", debounce=False)

    def hook(n, t):
        if where == "after_n_sentences" and n == 3:
            raise RuntimeError("injected (a)")

    kw = {} if where == "after_n_sentences" else {"fail_at": where}
    assert run(FakeSynth(hook=hook), worker="w2", **kw) == rw.ERROR
    b = book(env)
    assert b.job_status == "error" and "injected" in b.error_message
    assert (b.ready_generation, b.ready_fingerprint) == (1, "fpA")
    assert gen_files("b1", 1) == old_files
    assert sentence_rows(env) == old_rows
    assert (storage.RENDERS_DIR / "b1" / str(b.ready_generation)).is_dir()
    # a retry still publishes cleanly (resuming where possible)
    rj.request_render("b1", "fpB", debounce=False)
    assert run(FakeSynth(), worker="w3") == rw.READY
    assert book(env).ready_generation == 2


def test_audit_error_publishes_nothing(env, real_ffmpeg, monkeypatch):
    ffmpeg_required()
    mk_book(env)

    def bad(*a, **k):
        raise ra.RenderAuditError("tier 1.5: drift +99.0 ms")

    monkeypatch.setattr(ra, "drift_audit", bad)
    assert run(FakeSynth()) == rw.ERROR
    b = book(env)
    assert b.job_status == "error" and b.phase == "drift_audit" and b.ready_generation is None
    assert "RenderAuditError" in b.error_message and "Traceback" in b.error_message
    assert not (storage.RENDERS_DIR / "b1" / "1").exists()


# ------------------------------------------------------------------ resume
def test_resume_without_audiocache_and_with_tiny_cache(env, real_ffmpeg, monkeypatch):
    ffmpeg_required()
    monkeypatch.setenv("AUDIO_CACHE_MAX_MB", "1")
    mk_book(env)
    with Session(env) as s:  # a warm cache that must NOT be what resume depends on
        s.add(AudioCache(text_hash="h", audio_data=b"x", duration_ms=1, voice="v",
                         created_at=T0))
        s.commit()
    synth = FakeSynth(hook=lambda n, t: (_ for _ in ()).throw(RuntimeError("crash"))
                      if n == 5 else None)
    assert run(synth) == rw.ERROR
    assert len(tmp_dirs()) == 1  # the checkpoint survives
    with Session(env) as s:
        for row in s.exec(select(AudioCache)).all():
            s.delete(row)
        s.commit()
        assert s.exec(select(AudioCache)).all() == []
    rj.request_render("b1", "fpA", debounce=False)
    assert run(synth, worker="w2") == rw.READY
    # 6 sentences, one failed attempt at the 5th: 4 done + failing one retried + last
    assert sorted(synth.calls) == sorted(texts() + [texts()[4]])
    assert max(synth.calls.count(t) for t in texts() if t != texts()[4]) == 1


# --------------------------------------------------- supersede/cancel/delete
def test_supersede_mid_job_yields_one_render_for_new_target(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env)
    fired = []

    def hook(n, t):
        if n == 3 and not fired:
            fired.append(1)
            assert rj.request_render("b1", "fpB", debounce=False) == "processing"

    synth = FakeSynth(hook=hook)
    assert run(synth) == rw.REQUEUED
    b = book(env)
    assert b.job_status == "pending" and b.ready_generation is None and b.lease_owner is None
    assert run(synth, worker="w2") == rw.READY
    b = book(env)
    assert (b.ready_generation, b.ready_fingerprint, b.target_fingerprint) == (1, "fpB", "fpB")
    assert sorted(p.name for p in (storage.RENDERS_DIR / "b1").iterdir()
                  if p.name != "processing.json") == ["1"]
    assert json.loads(gen_files("b1", 1)["manifest.json"])["fingerprint"] == "fpB"
    # the fpA checkpoint was discarded (different fingerprint), so work restarted
    assert len(synth.calls) == 3 + 6


def test_cancel_keeps_previous_generation(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env)
    assert run(FakeSynth()) == rw.READY
    old_files, old_rows = gen_files("b1", 1), sentence_rows(env)
    rj.request_render("b1", "fpB", debounce=False)
    synth = FakeSynth(hook=lambda n, t: rj.cancel("b1") if n == 2 else None)
    assert run(synth, worker="w2") == rw.CANCELLED
    b = book(env)
    assert b.job_status == "cancelled" and (b.ready_generation, b.ready_fingerprint) == (1, "fpA")
    assert gen_files("b1", 1) == old_files and sentence_rows(env) == old_rows
    assert not tmp_dirs() and len(synth.calls) == 2


def _delete_book(eng, bid="b1"):
    with Session(eng) as s:
        for r in s.exec(select(Sentence).where(Sentence.book_id == bid)).all():
            s.delete(r)
        s.delete(s.get(Book, bid))
        s.commit()


def test_delete_mid_render_leaves_nothing(env):
    mk_book(env)
    synth = FakeSynth(hook=lambda n, t: _delete_book(env) if n == 3 else None)
    assert run(synth) == rw.GONE
    assert not (storage.RENDERS_DIR / "b1").exists()
    assert book(env) is None and sentence_rows(env) == []
    assert len(synth.calls) == 3


def test_delete_mid_render_worker_thread_survives(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env, "gone")
    synth = FakeSynth(hook=lambda n, t: _delete_book(env, "gone") if n == 2 else None)
    stop = threading.Event()
    th = threading.Thread(target=rw.worker_loop, args=(stop, 0.02),
                          kwargs=dict(synthesizer=synth, aligner=FakeAligner(), tiers=TIERS,
                                      debounce_s=0), daemon=True)
    th.start()
    try:
        deadline = time.time() + 20
        while book(env, "gone") is not None and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
        assert th.is_alive()
        assert not (storage.RENDERS_DIR / "gone").exists()
        # ...and it keeps working: a new book still renders
        mk_book(env, "next")
        deadline = time.time() + 30
        while book(env, "next").ready_generation is None and time.time() < deadline:
            time.sleep(0.05)
        assert book(env, "next").ready_generation == 1 and th.is_alive()
    finally:
        stop.set()
        th.join(10)
    assert not th.is_alive()


# ------------------------------------------------------------- guards/errors
def test_blocked_on_disk(env):
    mk_book(env)
    synth = FakeSynth()
    Usage = type("U", (), {"free": 0})
    assert run(synth, disk_usage=lambda p: Usage) == rw.BLOCKED
    b = book(env)
    assert b.phase == "blocked_on_disk" and b.job_status == "pending" and b.lease_owner is None
    assert synth.calls == [] and not tmp_dirs()
    # not claimable again until the debounce window passes
    assert rj.claim_next("w2", datetime.now(timezone.utc), debounce_s=60) is None


def test_base_speed_mismatch_fails(env):
    mk_book(env)
    assert run(FakeSynth(effective_speed=0.9)) == rw.ERROR
    b = book(env)
    assert b.job_status == "error" and b.phase == "base_speed_mismatch"
    assert b.ready_generation is None and not (storage.RENDERS_DIR / "b1" / "1").exists()


def test_synthesis_failure_without_fallback_fails_fast(env):
    mk_book(env)
    synth = FakeSynth(hook=lambda n, t: (_ for _ in ()).throw(RuntimeError("nope")))
    assert run(synth) == rw.ERROR
    assert len(synth.calls) == 1  # no retries without a fallback backend
    assert book(env).ready_generation is None


def test_modal_retries_then_local_fallback_marks_mixed(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env)
    sleeps = []

    def hook(n, t):
        raise ConnectionError("modal down")

    primary, local = FakeSynth(hook=hook), FakeSynth()
    assert run(primary, fallback_synthesizer=local, sleep=sleeps.append) == rw.READY
    assert len(primary.calls) == 1 + rw.MODAL_RETRIES  # then the fallback takes over for good
    assert len(sleeps) == rw.MODAL_RETRIES and sleeps == sorted(sleeps)
    assert len(local.calls) == 6
    m = json.loads(gen_files("b1", 1)["manifest.json"])
    assert m["mixed_backends"] is True


def test_lease_renewed_every_30s_of_work(env, monkeypatch):
    mk_book(env)
    renewals = []
    real = rj.renew_lease
    monkeypatch.setattr(rj, "renew_lease",
                        lambda *a, **k: renewals.append(1) or real(*a, **k))
    t = {"v": 0.0}

    def clock():
        t["v"] += 20.0
        return t["v"]

    synth = FakeSynth(hook=lambda n, t_: (_ for _ in ()).throw(RuntimeError("stop")) if n == 4 else None)
    run(synth, clock=clock)
    assert len(renewals) >= 2


# -------------------------------------------------------------------- loop
def test_processor_enabled_flag(monkeypatch):
    monkeypatch.delenv("PROCESSOR_ENABLED", raising=False)
    assert rw.processor_enabled() is True
    monkeypatch.setenv("PROCESSOR_ENABLED", "0")
    assert rw.processor_enabled() is False
    assert rw.start_worker() is None


def test_worker_loop_disabled_claims_nothing(env, monkeypatch):
    monkeypatch.setenv("PROCESSOR_ENABLED", "0")
    mk_book(env)
    stop = threading.Event()
    th = threading.Thread(target=rw.worker_loop, args=(stop, 0.01),
                          kwargs=dict(synthesizer=FakeSynth(), debounce_s=0), daemon=True)
    th.start()
    time.sleep(0.3)
    stop.set()
    th.join(5)
    assert book(env).job_status == "pending"


def test_worker_loop_survives_errors_and_stops(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env, "bad", target="x")
    bad = FakeSynth(hook=lambda n, t: (_ for _ in ()).throw(RuntimeError("bad book")))
    stop = threading.Event()
    th = threading.Thread(target=rw.worker_loop, args=(stop, 0.01),
                          kwargs=dict(synthesizer=bad, aligner=FakeAligner(), tiers=TIERS,
                                      debounce_s=0), daemon=True)
    th.start()
    try:
        deadline = time.time() + 20
        while book(env, "bad").job_status != "error" and time.time() < deadline:
            time.sleep(0.02)
        b = book(env, "bad")
        assert b.job_status == "error" and "bad book" in b.error_message and th.is_alive()
    finally:
        stop.set()
        th.join(10)
    assert not th.is_alive()


def test_start_stop_worker_hooks(env, monkeypatch):
    monkeypatch.setenv("PROCESSOR_ENABLED", "1")
    th = rw.start_worker(0.01, synthesizer=FakeSynth(), debounce_s=0)
    assert th is not None and th.is_alive() and rw.start_worker() is th
    rw.stop_worker()
    assert not th.is_alive()


def test_punctuation_only_sentence_is_skipped_not_fatal(env, real_ffmpeg):
    ffmpeg_required()
    mk_book(env)
    with Session(env) as s:
        s.exec(select(Sentence).where(Sentence.book_id == "b1", Sentence.index == 2))
        row = s.exec(select(Sentence).where(Sentence.book_id == "b1", Sentence.index == 2)).one()
        row.text = "."
        s.add(row)
        s.commit()
    synth = FakeSynth()
    assert run(synth) == rw.READY
    assert "." not in synth.calls and len(synth.calls) == 5


def test_heartbeat_keeps_the_lease_alive_through_encode(env, monkeypatch):
    """The encode and audit phases do not reach the per-sentence renewal; the heartbeat
    must renew the lease while they run."""
    mk_book(env)
    renewals = []
    real_renew = rj.renew_lease

    def spy(book_id, worker_id, now, lease_s=120):
        renewals.append(now)
        return real_renew(book_id, worker_id, now, lease_s)

    monkeypatch.setattr(rj, "renew_lease", spy)
    assert rj.claim_next("w1", T0 + timedelta(days=1), debounce_s=0) == "b1"
    hb = rw._Heartbeat("b1", "w1", lambda: T0 + timedelta(days=1), 120, 0.01)
    hb.start()
    time.sleep(0.1)
    hb.stop()
    assert len(renewals) >= 2


def test_queue_shows_a_long_encode_as_processing_not_queued(env, monkeypatch):
    """Regression: a render in its encode phase lost its lease after 120 s, and the
    status showed "queued" while ffmpeg was still running. The lease must stay live for
    as long as the worker runs, so the status must read processing throughout."""
    from routers import library as lib
    mk_book(env)
    assert rj.claim_next("w1", T0 + timedelta(days=1), debounce_s=0) == "b1"
    with Session(env) as s:
        b = s.get(Book, "b1")
        b.lease_expires = datetime.now(timezone.utc) + timedelta(seconds=120)
        s.add(b)
        s.commit()
        assert lib._render_status(s.get(Book, "b1"))["job_status"] == "processing"
        # the lease lapses (no heartbeat): the same row must now read as queued
        b = s.get(Book, "b1")
        b.lease_expires = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.add(b)
        s.commit()
        assert lib._render_status(s.get(Book, "b1"))["job_status"] == "pending"


def test_heartbeat_is_running_during_encode_and_audit(env, monkeypatch):
    """The render must start the heartbeat before the encode phase and stop it after."""
    started, stopped = [], []

    class Spy(rw._Heartbeat):
        def start(self):
            started.append(True)
            super().start()

        def stop(self):
            stopped.append(True)
            super().stop()

    monkeypatch.setattr(rw, "_Heartbeat", Spy)
    mk_book(env)
    assert run(FakeSynth()) == rw.READY
    assert started == [True] and stopped == [True]


def test_pause_requeues_mid_book_and_resume_finishes_it(env, real_ffmpeg, monkeypatch):
    """Pausing stops the render between sentences and requeues the book with its
    checkpoint; unpausing finishes it without re-synthesizing the finished sentences."""
    from services import render_pause
    ffmpeg_required()
    mk_book(env)
    synth = FakeSynth(hook=lambda n, t: render_pause.set_paused(True) if n == 2 else None)
    assert run(synth) == rw.REQUEUED
    assert len(synth.calls) == 2
    b = book(env)
    assert b.job_status == "pending" and b.lease_owner is None
    assert render_pause.is_paused()
    render_pause.set_paused(False)
    synth2 = FakeSynth()
    assert run(synth2, worker="w2") == rw.READY
    assert len(synth2.calls) == 4  # sentences 0 and 1 came from the checkpoint; 2-5 remain


def test_worker_loop_claims_nothing_while_paused(env, monkeypatch):
    from services import render_pause
    mk_book(env)
    render_pause.set_paused(True)
    monkeypatch.setattr(rw, "processor_enabled", lambda: True)
    monkeypatch.setattr(rw, "_engine_ready", lambda: True)
    stop = threading.Event()
    calls = []
    monkeypatch.setattr(rw, "run_once", lambda *a, **k: calls.append(1) or None)
    t = threading.Thread(target=rw.worker_loop, args=(stop,), kwargs={"poll_s": 0.01,
                                                                    "synthesizer": FakeSynth()})
    t.start()
    time.sleep(0.1)
    stop.set()
    t.join(2)
    assert calls == []


# ------------------------------------------------------- stall watchdog
def test_heartbeat_marks_a_render_with_no_progress_as_stalled(env):
    mk_book(env)
    assert rj.claim_next("w1", T0 + timedelta(days=1), debounce_s=0) == "b1"
    hb = rw._Heartbeat("b1", "w1", lambda: T0 + timedelta(days=1), 120, 0.01, stall_s=0.05)
    hb.touch("synthesize")
    hb.start()
    deadline = time.time() + 2
    while not hb.stalled and time.time() < deadline:
        time.sleep(0.01)
    hb.stop()
    b = book(env)
    assert hb.stalled
    assert b.job_status == "error" and b.phase == "synthesize"
    assert "stalled" in b.error_message and b.lease_owner is None


def test_heartbeat_does_not_stall_while_progress_is_reported(env):
    mk_book(env)
    assert rj.claim_next("w1", T0 + timedelta(days=1), debounce_s=0) == "b1"
    hb = rw._Heartbeat("b1", "w1", lambda: T0 + timedelta(days=1), 120, 0.01, stall_s=0.2)
    hb.start()
    for _ in range(15):
        time.sleep(0.02)
        hb.touch("synthesize")
    hb.stop()
    assert not hb.stalled
    assert book(env).job_status == "processing"


def test_a_hung_synthesis_call_surfaces_as_an_error_not_a_frozen_bar(env):
    mk_book(env)
    synth = FakeSynth(hook=lambda n, t: time.sleep(0.4) if n == 1 else None)
    assert run(synth, renew_every_s=0.01, stall_s=0.1) == rw.ERROR
    b = book(env)
    assert b.job_status == "error" and "stalled" in b.error_message
    assert b.ready_generation is None
    assert len(synth.calls) == 1  # the render bailed out once the call came back


# ------------------------------------------------- chunked encode + watchdog
def test_chunked_encode_feeds_the_stall_watchdog(env, real_ffmpeg, monkeypatch):
    """A long book's tiers are encoded in parallel chunks; each spliced chunk is a
    progress step in the encode phase (bar + heartbeat touch), and the audit then
    checks the spliced files themselves."""
    from services import tier_encode as te
    ffmpeg_required()
    monkeypatch.setenv("RENDER_ENCODE_WORKERS", "3")
    monkeypatch.setattr(te, "CHUNK_S", 1.0)
    monkeypatch.setattr(te, "PROGRESS_EVERY_S", 0.0)
    runs = []
    real_encode = te.encode_tiers
    monkeypatch.setattr(te, "encode_tiers", lambda *a, **k: runs.append(real_encode(*a, **k))
                        or runs[-1])
    touches = []
    real_touch = rw._Heartbeat.touch

    def touch(self, phase=None):
        touches.append(phase)
        real_touch(self, phase)

    monkeypatch.setattr(rw._Heartbeat, "touch", touch)
    marks = []
    real_mark = rj.mark_progress

    def mark(book_id, frac, phase):
        marks.append((frac, phase))
        return real_mark(book_id, frac, phase)

    monkeypatch.setattr(rj, "mark_progress", mark)
    mk_book(env, n=8)
    assert run(FakeSynth(), tiers=(1.5, 2.0)) == rw.READY
    assert len(runs) == 1
    chunks = sum(t["chunks"] for t in runs[0].values())
    assert chunks >= 10
    enc = [f for f, ph in marks if ph == "encode"]
    assert all(0.90 <= f <= 0.95 + 1e-9 for f in enc), enc
    in_tiers = [f for f in enc if 0.92 < f < 0.95]
    assert in_tiers == sorted(in_tiers)
    assert len(in_tiers) >= chunks - 1     # one step per spliced chunk
    assert touches.count("encode") >= len(enc)
    files = gen_files("b1", 1)
    assert {"audio_1.0.opus", "audio_1.5.opus", "audio_2.0.opus"} <= set(files)
    assert not any(n.endswith(".part") or n.startswith("audit_") for n in files)
