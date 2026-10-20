"""Phase 5: parallel sentence synthesis inside one book (parallel-safe CPU engines only).

The consumer stays single-threaded and ordered; only ``synthesizer(text, voice)`` fans out.
Everything real downstream (aligner call, chunk files, tiers, manifest, publish) is exercised
through ``render_book`` with deterministic fakes.
"""
import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlmodel import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.database as _db  # noqa: E402
import storage  # noqa: E402
from db.models import Book, Sentence  # noqa: E402
from scripts import render_baseline as rb  # noqa: E402
from services import export_encoding, render_jobs as rj, render_manifest, render_pause  # noqa: E402
from services import render_worker as rw  # noqa: E402

SR = 24000
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
TIERS = (1.5,)
pytestmark = pytest.mark.skipif(not export_encoding.ffmpeg_available(), reason="needs ffmpeg")


# ------------------------------------------------------------------- fakes
class PSynth(rb.FakeSynth):
    """Deterministic fake that is parallel-safe, with random per-call delays, an optional
    failure on one text, and bookkeeping of who ran what and how many ran at once."""

    def __init__(self, delay=0.0, seed=1, fail_text=None, hook=None):
        self.rng, self.delay, self.fail_text, self.hook = random.Random(seed), delay, fail_text, hook
        self.lock = threading.Lock()
        self.calls, self.threads = [], set()
        self.active = self.max_active = 0

    def for_worker(self, threads=None):
        return self

    def __call__(self, text, voice):
        with self.lock:
            self.calls.append(text)
            self.threads.add(threading.get_ident())
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            d = self.rng.random() * self.delay
        try:
            if self.hook:
                self.hook(text)
            time.sleep(d)
            if text == self.fail_text:
                raise RuntimeError(f"boom on {text!r}")
            return super().__call__(text, voice)
        finally:
            with self.lock:
                self.active -= 1


class SerialSynth(PSynth):
    parallel_safe = False  # explicit: Torch-Kokoro-like


class NoAttrSynth(rb.FakeSynth):
    parallel_safe = False
    threads = set()

    def __call__(self, text, voice):
        NoAttrSynth.threads.add(threading.get_ident())
        return super().__call__(text, voice)


def synth_threads_alive():
    return [t for t in threading.enumerate() if t.name.startswith("render-synth")]


@pytest.fixture
def env(db_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "engine", db_engine)
    monkeypatch.setattr(storage, "RENDERS_DIR", tmp_path / "renders")
    (tmp_path / "renders").mkdir()
    monkeypatch.delenv("RENDER_SYNTH_WORKERS", raising=False)
    monkeypatch.setattr(rw, "_warned_serial", False)
    return db_engine


def mk_book(eng, n=12, bid="b1"):
    with Session(eng) as s:
        s.add(Book(id=bid, title=bid, file_path="missing.pdf", file_type="text", page_count=1,
                   created_at=T0, target_fingerprint="fpA", job_status="pending"))
        s.add_all(Sentence(book_id=bid, index=i, text=f"Sentence number {i} is read aloud here.",
                           page=0, x0=0, y0=0, x1=0, y1=0, filtered=False, chapter=i // 5)
                  for i in range(n))
        s.commit()


def run(synth, bid="b1", **kw):
    kw.setdefault("aligner", rb.FakeAligner())
    kw.setdefault("tiers", TIERS)
    kw.setdefault("raise_errors", True)
    assert rj.claim_next("w1", T0 + timedelta(days=1), debounce_s=0) == bid
    return rw.render_book(bid, "w1", synthesizer=synth, **kw)


def published(bid="b1"):
    gens = sorted(p for p in (storage.RENDERS_DIR / bid).iterdir() if p.name.isdigit())
    return gens[-1]


def manifest(bid="b1"):
    m = json.loads((published(bid) / "manifest.json").read_text())
    return rb._scrub(m)


def base_pcm(bid="b1"):
    return rb._pcm_sha(published(bid) / "base.flac")


# ------------------------------------------------------------------- tests
def test_results_are_consumed_in_index_order_despite_random_delays(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    mk_book(env, 16)
    order = []
    orig = rw._clamp_words  # consumer-only, once per fresh sentence, after its checks
    monkeypatch.setattr(rw, "_clamp_words", lambda w, d: (order.append(w[0]["word"]), orig(w, d))[1])
    order_idx = []
    orig_wj = rw._write_json
    monkeypatch.setattr(rw, "_write_json", lambda p, o: (order_idx.append(p.name), orig_wj(p, o))[1])
    synth = PSynth(delay=0.08)
    assert run(synth) == rw.READY
    chunk_order = [int(n.split(".")[0]) for n in order_idx if n.endswith(".json") and n[0].isdigit()]
    assert chunk_order == list(range(16))
    assert len(order) == 16
    assert synth.max_active > 1, "never ran two syntheses at once"
    assert [s["index"] for s in render_manifest.tier_segments(manifest(), "1.0")] == list(range(16))
    assert not synth_threads_alive()


def test_lookahead_window_is_bounded(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "2")
    mk_book(env, 14)
    consumed = []
    orig = rw._clamp_words
    monkeypatch.setattr(rw, "_clamp_words", lambda w, d: (consumed.append(1), orig(w, d))[1])
    ahead = []
    synth = PSynth(hook=lambda t: ahead.append(int(t.split()[2]) - len(consumed)))
    assert run(synth) == rw.READY
    assert max(ahead) <= 2 * 2, f"scheduled {max(ahead)} sentences ahead of the consumer"


def test_n1_and_n4_produce_identical_output_through_render_once(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "engine", _db.engine)
    monkeypatch.setattr(storage, "RENDERS_DIR", storage.RENDERS_DIR)
    corpus = rb.CORPUS[:12]
    snaps = {}
    for n in (1, 4):
        monkeypatch.setenv("RENDER_SYNTH_WORKERS", str(n))
        root = tmp_path / f"n{n}"
        root.mkdir()
        gen, aligned = rb.render_once("fake", corpus, root)
        snaps[n] = rb.snapshot(gen, "fake", corpus, aligned)
    rep = rb.diff_snapshots(snaps[1], snaps[4], exact=True)
    assert rep.ok, rep.failures
    assert snaps[1]["manifest"] == snaps[4]["manifest"]
    assert snaps[1]["aligned"] == snaps[4]["aligned"]
    assert {k: v["pcm_sha256"] for k, v in snaps[1]["files"].items()} == \
           {k: v["pcm_sha256"] for k, v in snaps[4]["files"].items()}


def test_error_in_sentence_k_matches_serial_error_and_stops_threads(env, real_ffmpeg, monkeypatch):
    bad = "Sentence number 5 is read aloud here."
    errs = {}
    for n in (1, 4):
        monkeypatch.setenv("RENDER_SYNTH_WORKERS", str(n))
        bid = f"b{n}"
        mk_book(env, 12, bid)
        with pytest.raises(RuntimeError) as ei:
            run(PSynth(delay=0.05, fail_text=bad), bid=bid)
        errs[n] = str(ei.value)
        done = sorted(int(p.stem) for p in (storage.RENDERS_DIR / bid).glob(".tmp-*/chunks/*.json"))
        assert done == [0, 1, 2, 3, 4], f"N={n}: chunks written {done}"
        assert not synth_threads_alive()
    assert errs[1] == errs[4]


def test_error_phase_is_recorded_like_serial(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    mk_book(env, 8)

    class NoAudio(PSynth):
        def __call__(self, text, voice):
            raise rw.ra.RenderError("synthesis produced no audio", phase="synthesize")

    assert run(NoAudio(), raise_errors=False) == rw.ERROR
    with Session(env) as s:
        b = s.get(Book, "b1")
        assert b.job_status == "error" and b.phase == "synthesize"
    assert not synth_threads_alive()


def test_resume_under_n4_skips_finished_and_equals_uninterrupted(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    mk_book(env, 12, "clean")
    assert run(PSynth(delay=0.02), bid="clean") == rw.READY
    clean_pcm, clean_m = base_pcm("clean"), manifest("clean")

    mk_book(env, 12, "res")
    with pytest.raises(RuntimeError):
        run(PSynth(delay=0.02, fail_text="Sentence number 7 is read aloud here."), bid="res")
    with Session(env) as s:  # back to pending; chunk files stay on disk
        b = s.get(Book, "res")
        b.job_status, b.lease_owner, b.lease_expires = "pending", None, None
        s.add(b)
        s.commit()
    second = PSynth(delay=0.02)
    assert run(second, bid="res") == rw.READY
    assert sorted(second.calls, key=lambda t: int(t.split()[2])) == [f"Sentence number {i} is read aloud here." for i in range(7, 12)], \
        "resume must synthesize exactly the unfinished sentences"
    assert base_pcm("res") == clean_pcm
    assert manifest("res") == clean_m


def test_cancel_mid_window_stops_promptly_without_leftover_threads(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    mk_book(env, 40)
    seen = []

    def hook(text):
        seen.append(text)
        if len(seen) == 6:
            rj.cancel("b1")

    synth = PSynth(delay=0.3, hook=hook)
    t0 = time.monotonic()
    assert run(synth, raise_errors=False) == rw.CANCELLED
    assert time.monotonic() - t0 < 5.0
    assert len(synth.calls) < 20, "kept scheduling after cancel"
    assert not synth_threads_alive()
    assert not list((storage.RENDERS_DIR / "b1").glob(".tmp-*"))


def test_pause_mid_window_requeues_and_keeps_checkpoint(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    mk_book(env, 30)
    n = []
    synth = PSynth(delay=0.1, hook=lambda t: (n.append(t), render_pause.set_paused(True))
                   if len(n) == 5 else n.append(t))
    try:
        assert run(synth, raise_errors=False) == rw.REQUEUED
    finally:
        render_pause.set_paused(False)
    assert not synth_threads_alive()
    chunks = list((storage.RENDERS_DIR / "b1").glob(".tmp-*/chunks/*.json"))
    flacs = {p.stem for p in (storage.RENDERS_DIR / "b1").glob(".tmp-*/chunks/*.flac")}
    assert chunks and {p.stem for p in chunks} <= flacs, "sidecar without its flac"


def test_non_parallel_safe_engine_stays_serial_and_warns_once(env, real_ffmpeg, monkeypatch, caplog):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    mk_book(env, 8)
    NoAttrSynth.threads = set()
    with caplog.at_level("WARNING", logger="services.render_worker"):
        assert run(NoAttrSynth()) == rw.READY
    assert NoAttrSynth.threads == {threading.get_ident()}
    assert sum("not parallel-safe" in r.message for r in caplog.records) == 1
    assert not synth_threads_alive()


def test_serial_synth_flagged_false_is_not_parallelised(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "8")
    mk_book(env, 8)
    s = SerialSynth(delay=0.01)
    assert run(s) == rw.READY
    assert s.threads == {threading.get_ident()} and s.max_active == 1


def test_default_workers_is_the_serial_path(env, real_ffmpeg):
    mk_book(env, 8)
    s = PSynth()
    assert run(s) == rw.READY
    assert s.threads == {threading.get_ident()}


@pytest.mark.parametrize("raw,expect", [(None, 1), ("", 1), ("abc", 1), ("0", 1), ("-3", 1),
                                        ("3", 3), ("2.0", 2), ("1e400", 1)])
def test_workers_env_parsing(monkeypatch, raw, expect):
    if raw is None:
        monkeypatch.delenv("RENDER_SYNTH_WORKERS", raising=False)
    else:
        monkeypatch.setenv("RENDER_SYNTH_WORKERS", raw)
    assert rw.synth_workers_setting() == expect


def test_thread_budget_defaults_to_cpu_count_and_divides(monkeypatch):
    import os
    monkeypatch.delenv("RENDER_THREADS_TOTAL", raising=False)
    assert rw.synth_threads_total() == (os.cpu_count() or 1)
    monkeypatch.setenv("RENDER_THREADS_TOTAL", "12")
    assert rw.synth_threads_total() == 12


def test_workers_receive_the_divided_thread_budget(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    monkeypatch.setenv("RENDER_THREADS_TOTAL", "10")
    mk_book(env, 6)
    got = []

    class S(PSynth):
        def for_worker(self, threads=None):
            got.append(threads)
            return self

    assert run(S()) == rw.READY
    assert got and set(got) == {2}  # 10 // 4


def test_heartbeat_is_touched_by_worker_completions(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "3")
    mk_book(env, 6)
    touches = []
    orig = rw._Heartbeat.touch
    monkeypatch.setattr(rw._Heartbeat, "touch",
                        lambda self, phase=None: (touches.append(threading.current_thread().name),
                                                  orig(self, phase))[1])
    assert run(PSynth()) == rw.READY
    assert any(n.startswith("render-synth") for n in touches)


# -------------------------------------------------- Piper engine + live-pool isolation
def test_piper_voice_cache_loads_once_under_concurrent_first_use(tmp_path, monkeypatch):
    import piper.voice as pv
    from services import piper_engine as pe

    (tmp_path / "x.onnx").write_bytes(b"")
    loads = []

    class FakeVoice:
        config = SimpleNamespace(sample_rate=22050)

        @staticmethod
        def load(path, **kw):
            loads.append(path)
            time.sleep(0.05)
            return FakeVoice()

    monkeypatch.setattr(pv, "PiperVoice", FakeVoice)
    eng = pe.PiperEngine(str(tmp_path), warm=False)
    got, barrier = [], threading.Barrier(16)

    def hit(fn):
        barrier.wait()
        got.append(fn("x"))

    ts = [threading.Thread(target=hit, args=(eng._load if i % 2 else eng._load_aligned,))
          for i in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(loads) == 2  # one plain + one aligned, never duplicated
    assert len({id(v) for v in got}) == 2


def test_clone_for_worker_is_independent_and_capped(tmp_path, monkeypatch):
    from services import piper_engine as pe

    (tmp_path / "x.onnx").write_bytes(b"")
    base = pe.PiperEngine(str(tmp_path), warm=False)
    c = base.clone_for_worker(3)
    assert c is not base and c._loaded is not base._loaded and c._threads == 3
    assert base.parallel_safe and c.parallel_safe


def test_capped_session_really_limits_intra_op_threads():
    voices = sorted(Path(os.environ.get("PIPER_VOICES_DIR") or storage.PIPER_VOICES_DIR).glob("*.onnx"))
    if not voices:
        pytest.skip("no piper voice installed")
    from services import piper_engine as pe

    s = pe._capped_session(voices[0], False, 3)
    assert s.get_session_options().intra_op_num_threads == 3


def test_tts_engine_exposes_parallel_safe_from_its_engine():
    from services.tts_engine import TTSEngine

    assert not TTSEngine(lambda *a, **k: []).parallel_safe  # torch Kokoro-like: default False
    assert not TTSEngine(SimpleNamespace(parallel_safe=True)).parallel_safe  # no clone support
    eng = TTSEngine(SimpleNamespace(parallel_safe=True, clone_for_worker=lambda n: n))
    assert eng.parallel_safe and eng.clone_for_render_worker(5) == 5
    with pytest.raises(RuntimeError):
        TTSEngine(lambda *a, **k: []).clone_for_render_worker(1)


def test_live_synthesis_stays_on_the_single_worker_pool_during_a_parallel_render(
        env, real_ffmpeg, monkeypatch):
    from services import tts_engine

    assert tts_engine._synthesis_pool._max_workers == 1
    submitted = []

    class SpyPool:
        _max_workers = 1

        def submit(self, fn, *a, **k):
            submitted.append(fn)
            return real_pool.submit(fn, *a, **k)

    real_pool = tts_engine._synthesis_pool
    monkeypatch.setattr(tts_engine, "_synthesis_pool", SpyPool())

    class Clone:
        def synthesize_for_render(self, text, voice):
            time.sleep(0.01)
            return rb.FakeSynth()(text, voice).audio, None

    clones = []
    kokoro = SimpleNamespace(parallel_safe=True,
                             clone_for_worker=lambda n: (clones.append(n), Clone())[1])
    eng = tts_engine.TTSEngine(kokoro)
    eng.synthesize_cached = lambda text, voice, speed: (np.zeros(10, np.float32), 1.0)
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "3")
    mk_book(env, 10)
    live = []
    stop = threading.Event()

    def live_loop():  # live playback running while the render is in flight
        while not stop.is_set():
            live.append(tts_engine.synthesize_serialized(eng, "hi", "v", 1.0))
            time.sleep(0.005)

    th = threading.Thread(target=live_loop)
    th.start()
    try:
        assert run(rw.engine_synthesizer(eng)) == rw.READY
    finally:
        stop.set()
        th.join()
    assert len(clones) >= 2, "render did not fan out over engine clones"
    # every pool task was a live one (synthesize_serialized submits the bound method)
    assert submitted and all(getattr(f, "__name__", "") == "<lambda>" or
                             f is eng.synthesize_cached for f in submitted)
    assert len(submitted) == len(live)


# ----------------------------------------------------- alignment moves to the workers
def test_alignment_runs_on_worker_threads_and_per_thread_budget_is_capped(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    monkeypatch.setenv("RENDER_THREADS_TOTAL", "112")
    mk_book(env, 8)
    align_threads, got = set(), []

    class A(rb.FakeAligner):
        def align(self, audio, sr, words):
            align_threads.add(threading.current_thread().name)
            return super().align(audio, sr, words)

    class S(PSynth):
        def for_worker(self, threads=None):
            got.append(threads)
            return self

    assert run(S(delay=0.01), aligner=A()) == rw.READY
    assert all(n.startswith("render-synth") for n in align_threads)
    assert set(got) == {rw.MAX_THREADS_PER_WORKER}


def test_speed_mismatch_error_is_unchanged_under_n4(env, real_ffmpeg, monkeypatch):
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "4")
    mk_book(env, 8)

    class Fast(PSynth):
        def __call__(self, text, voice):
            r = super().__call__(text, voice)
            r.effective_speed = 1.5
            return r

    assert run(Fast(), raise_errors=False) == rw.ERROR
    with Session(env) as s:
        assert s.get(Book, "b1").phase == "base_speed_mismatch"


def test_real_ctc_aligner_is_safe_and_deterministic_under_threads():
    from services import aligner as al
    model = Path.home() / "ctc_forced_aligner" / "model.onnx"
    if not model.is_file():
        pytest.skip("CTC aligner model not downloaded")
    texts = [t for t in rb.CORPUS[:8] if t not in rb.FILTERED]
    audios = [rb.FakeSynth()(t, "v").audio for t in texts]

    def one(a, t):
        try:
            return al.CtcOnnxAligner(threads=1).align(a, SR, t.split())
        except ValueError:
            return "short"

    ref = [one(a, t) for a, t in zip(audios, texts)]
    out, errs = {}, []

    def work(i):
        try:
            out[i] = [one(a, t) for a, t in zip(audios, texts)]
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and all(out[i] == ref for i in out)


def test_default_aligner_singleton_is_created_once_under_contention(monkeypatch):
    from services import aligner as al
    monkeypatch.setattr(al, "_default", None)
    got, barrier = [], threading.Barrier(12)

    def hit():
        barrier.wait()
        got.append(al.default_aligner())

    ts = [threading.Thread(target=hit) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len({id(g) for g in got}) == 1


def test_shared_aligner_is_one_object_sized_by_workers(monkeypatch):
    monkeypatch.setenv("RENDER_THREADS_TOTAL", "112")
    monkeypatch.setattr("services.aligner._default", None)
    a4 = rw._shared_aligner(4)          # 2 x 4 < default 8 -> the shared default singleton
    assert a4 is rw._shared_aligner(2)
    a16 = rw._shared_aligner(16)        # 32 threads: a render-local aligner, not the singleton
    assert a16.threads == 32 and a16 is not a4
    monkeypatch.setenv("RENDER_THREADS_TOTAL", "6")
    assert rw._shared_aligner(16).threads == 6  # never above the budget


def test_alignment_model_patch_is_computed_once_and_shared(tmp_path, monkeypatch):
    import onnx
    from services import piper_engine as pe

    calls = []
    monkeypatch.setattr(onnx, "load", lambda p: (calls.append(p), time.sleep(0.05), SimpleNamespace(
        SerializeToString=lambda: b"patched"))[2])
    import piper.patch_voice_with_alignment as pva
    monkeypatch.setattr(pva, "add_alignment_output", lambda m: "t")
    model = tmp_path / "v.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(pe, "_patched", {})
    out = []
    ts = [threading.Thread(target=lambda: out.append(pe._alignment_model(model))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and out == [b"patched"] * 8
