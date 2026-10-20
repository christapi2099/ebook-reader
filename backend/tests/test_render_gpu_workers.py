"""GPU (torch Kokoro) render workers: default of 4, explicit override, VRAM cap, OOM step-down,
and unchanged output order. No torch and no GPU: the worker processes are replaced by fakes."""
import json
import sys
import threading
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
from services import export_encoding, kokoro_process_pool as kpp, render_jobs as rj  # noqa: E402
from services import render_worker as rw  # noqa: E402
from services.tts_engine import TTSEngine  # noqa: E402

GIB = 1024 ** 3
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeOOM(Exception):
    """Named like torch's class so ``is_oom`` recognises it (the real one needs torch)."""


FakeOOM.__name__ = "OutOfMemoryError"


class FakeHandle:
    """Stands in for a worker process. ``oom_texts`` are sentences that OOM on this handle."""
    created: list = []

    def __init__(self, device, oom_texts=()):
        self.device, self.oom_texts, self.closed = device, set(oom_texts), False
        self.threads = set()
        FakeHandle.created.append(self)

    def wait_ready(self, timeout=0):
        return True, None

    def request(self, text, voice):
        self.threads.add(threading.get_ident())
        if text in self.oom_texts:
            self.oom_texts.discard(text)  # OOMs once on this handle, then the process is gone
            return ("oom", "HIP out of memory")
        a = rb.FakeSynth()(text, voice).audio
        return ("ok", a, [(w, i * 0.1, i * 0.1 + 0.1) for i, w in enumerate(text.split())])

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    FakeHandle.created = []
    monkeypatch.delenv("RENDER_SYNTH_WORKERS", raising=False)
    monkeypatch.delenv("RENDER_GPU_WORKER_VRAM_GB", raising=False)
    monkeypatch.setattr(rw, "_warned_serial", False)


class FakeKokoro:
    """The in-process pipeline (serial path): same tone as the fake worker processes."""

    def __init__(self, device):
        self.render_device = device

    def __call__(self, text, voice="", speed=1.0):
        tokens = [SimpleNamespace(text=w, phonemes="a", start_ts=i * 0.1, end_ts=i * 0.1 + 0.1)
                  for i, w in enumerate(text.split())]
        yield _Result(rb.FakeSynth()(text, voice).audio, tokens)


class _Result(tuple):
    def __new__(cls, audio, tokens):
        r = super().__new__(cls, ("g", "p", audio))
        r.tokens = tokens
        return r


def gpu_engine(device="cuda"):
    return TTSEngine(FakeKokoro(device))


# ------------------------------------------------------------- defaults
def test_gpu_engine_defaults_to_four_workers_and_others_to_one():
    assert gpu_engine().default_render_workers() == 4
    assert gpu_engine("cuda:1").default_render_workers() == 4
    assert gpu_engine("cpu").default_render_workers() == 1
    assert TTSEngine(SimpleNamespace()).default_render_workers() == 1  # Modal / fakes
    assert TTSEngine(SimpleNamespace(parallel_safe=True,
                                     clone_for_worker=lambda t: None)).default_render_workers() == 1


def test_resolve_uses_default_for_gpu_and_explicit_env_always_wins(monkeypatch):
    synth = rw.engine_synthesizer(gpu_engine())
    assert synth.parallel_safe
    assert rw.resolve_synth_workers(synth) == 4
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "2")
    assert rw.resolve_synth_workers(synth) == 2
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "1")
    assert rw.resolve_synth_workers(synth) == 1
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "6")
    assert rw.resolve_synth_workers(synth) == 6


def test_other_engines_keep_one_worker_unless_env_set(monkeypatch):
    cpu = rw.engine_synthesizer(gpu_engine("cpu"))
    assert rw.resolve_synth_workers(cpu) == 1
    assert rw.resolve_synth_workers(rb.FakeSynth()) == 1  # parallel-safe fake, no default
    monkeypatch.setenv("RENDER_SYNTH_WORKERS", "3")
    assert rw.resolve_synth_workers(rb.FakeSynth()) == 3


# ------------------------------------------------------------- VRAM cap
def test_vram_cap_formula():
    per = int(1.8 * GIB)
    assert kpp.vram_worker_cap(12 * GIB, per) == 6          # (12-1)/1.8 = 6.1
    assert kpp.vram_worker_cap(int(8.5 * GIB), per) == 4    # 7.5/1.8 = 4.2
    assert kpp.vram_worker_cap(int(5 * GIB), per) == 2      # 4/1.8 = 2.2
    assert kpp.vram_worker_cap(int(2.5 * GIB), per) == 1    # below one worker: still 1
    assert kpp.vram_worker_cap(0, per) == 1


def test_pool_is_capped_by_free_vram_and_logs(caplog):
    caplog.set_level("WARNING")
    pool = kpp.KokoroProcessPool("cuda", 4, spawn=FakeHandle, free_vram=lambda d: int(5 * GIB),
                                 per_worker_bytes=int(1.8 * GIB))
    assert pool.active == 2 and len(FakeHandle.created) == 2
    assert "GPU workers reduced 4 -> 2" in caplog.text


def test_pool_with_ample_vram_starts_requested_workers():
    pool = kpp.KokoroProcessPool("cuda", 4, spawn=FakeHandle, free_vram=lambda d: 12 * GIB)
    assert pool.active == 4 and len(FakeHandle.created) == 4
    pool.close()
    assert all(h.closed for h in FakeHandle.created)


def test_cap_of_one_starts_no_processes():
    pool = kpp.KokoroProcessPool("cuda", 4, spawn=FakeHandle, free_vram=lambda d: 2 * GIB)
    assert pool.active == 0 and FakeHandle.created == []


def test_engine_open_render_workers_returns_capped_count(monkeypatch):
    monkeypatch.setattr(kpp, "ProcessWorker", FakeHandle)
    monkeypatch.setattr(kpp, "free_vram_bytes", lambda d: int(5 * GIB))
    eng = gpu_engine()
    assert eng.open_render_workers(4) == 2
    eng.close_render_workers()
    assert all(h.closed for h in FakeHandle.created)
    monkeypatch.setattr(kpp, "free_vram_bytes", lambda d: 2 * GIB)
    assert eng.open_render_workers(4) == 1   # serial in-process; nothing left running
    assert eng._render_pool is None


# ------------------------------------------------------------- OOM step-down
def test_oom_steps_down_one_worker_and_retries_the_sentence(caplog):
    caplog.set_level("WARNING")
    made = []

    def spawn(device):
        h = FakeHandle(device, oom_texts={"boom"} if not made else ())
        made.append(h)
        return h

    pool = kpp.KokoroProcessPool("cuda", 3, spawn=spawn, free_vram=lambda d: 12 * GIB)
    audio, tokens = pool.synthesize("boom", "af_heart")
    assert audio.size > 0 and tokens
    assert pool.active == 2 and pool.oom_events == 1
    assert made[0].closed and not made[1].closed          # the OOMing process was retired
    assert "GPU workers 3 -> 2" in caplog.text


def test_oom_never_goes_below_one_worker_and_eventually_raises():
    class AlwaysOOM(FakeHandle):
        def request(self, text, voice):
            return ("oom", "out of memory")

    pool = kpp.KokoroProcessPool("cuda", 2, spawn=AlwaysOOM, free_vram=lambda d: 12 * GIB)
    with pytest.raises(RuntimeError, match="out of memory"):
        pool.synthesize("x", "v")
    assert pool.active == 1


def test_lone_worker_oom_restarts_and_succeeds():
    made = []

    def spawn(device):
        h = FakeHandle(device, oom_texts={"t"} if not made else ())
        made.append(h)
        return h

    pool = kpp.KokoroProcessPool("cuda", 2, spawn=spawn, free_vram=lambda d: 12 * GIB)
    pool.active = 1
    pool._all = [pool._all[0]]
    pool._idle.get()  # drop the second handle: one worker left
    audio, _ = pool.synthesize("t", "v")
    assert audio.size > 0 and pool.active == 1


def test_is_oom_matches_torch_class_and_hip_text():
    assert kpp.is_oom(FakeOOM("x"))
    assert kpp.is_oom(RuntimeError("HIP error: out of memory"))
    assert not kpp.is_oom(RuntimeError("something else"))


# ------------------------------------------------------------- end to end ordering
pytestmark_ffmpeg = pytest.mark.skipif(not export_encoding.ffmpeg_available(), reason="needs ffmpeg")


@pytest.fixture
def env(db_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "engine", db_engine)
    monkeypatch.setattr(storage, "RENDERS_DIR", tmp_path / "renders")
    (tmp_path / "renders").mkdir()
    return db_engine


def mk_book(eng, n, bid):
    with Session(eng) as s:
        s.add(Book(id=bid, title=bid, file_path="missing.pdf", file_type="text", page_count=1,
                   created_at=T0, target_fingerprint="fpA", job_status="pending"))
        s.add_all(Sentence(book_id=bid, index=i, text=f"Sentence number {i} is read aloud here.",
                           page=0, x0=0, y0=0, x1=0, y1=0, filtered=False, chapter=i // 5)
                  for i in range(n))
        s.commit()


def render_with_gpu_engine(monkeypatch, bid, workers_env):
    """A real TTSEngine tagged as GPU, with the real pool over fake processes."""
    monkeypatch.setattr(kpp, "ProcessWorker", FakeHandle)
    monkeypatch.setattr(kpp, "free_vram_bytes", lambda d: 12 * GIB)
    if workers_env is None:
        monkeypatch.delenv("RENDER_SYNTH_WORKERS", raising=False)
    else:
        monkeypatch.setenv("RENDER_SYNTH_WORKERS", workers_env)
    synth = rw.engine_synthesizer(gpu_engine())
    assert rj.claim_next("w1", T0 + timedelta(days=1), debounce_s=0) == bid
    return rw.render_book(bid, "w1", synthesizer=synth, aligner=rb.FakeAligner(), tiers=(1.5,),
                          raise_errors=True)


def gen_manifest(bid):
    gens = sorted(p for p in (storage.RENDERS_DIR / bid).iterdir() if p.name.isdigit())
    return rb._scrub(json.loads((gens[-1] / "manifest.json").read_text())), \
        rb._pcm_sha(gens[-1] / "base.flac")


@pytestmark_ffmpeg
def test_four_gpu_workers_give_same_order_manifest_and_audio_as_one(env, real_ffmpeg, monkeypatch):
    out = {}
    for label, workers, bid in (("one", "1", "b1"), ("default4", None, "b4")):
        mk_book(env, 14, bid)
        FakeHandle.created = []
        assert render_with_gpu_engine(monkeypatch, bid, workers) == rw.READY
        out[label] = (gen_manifest(bid), list(FakeHandle.created))
    (m1, pcm1), h1 = out["one"]
    (m4, pcm4), h4 = out["default4"]
    assert h1 == []                      # RENDER_SYNTH_WORKERS=1: serial path, no processes
    assert len(h4) == 4                  # default on the GPU engine
    assert all(h.closed for h in h4)     # VRAM released when the render ends
    segs = lambda m: [s["index"] for s in m["tiers"]["1.0"]["segments"]]
    assert segs(m4) == segs(m1) == list(range(14))
    assert pcm4 == pcm1
    assert m4["tiers"] == m1["tiers"]
    assert sum(1 for h in h4 if h.threads) > 1   # work really was spread over several workers
