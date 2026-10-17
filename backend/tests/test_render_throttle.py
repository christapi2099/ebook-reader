"""Worker etiquette for the render worker (Phase 1b): hermetic, fake synthesizers."""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from services import render_throttle as rt
from services import tts_engine


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    async def sleep(self, d):
        self.t += d


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_idle_returns_immediately():
    clock = Clock()
    waited = run(rt.await_playback_idle(5.0, waiting=lambda: 0, now=clock.now, sleep=clock.sleep))
    assert waited == 0.0


def test_waits_until_playback_clears():
    clock = Clock()
    waited = run(rt.await_playback_idle(
        5.0, waiting=lambda: 1 if clock.t < 1.0 else 0, now=clock.now, sleep=clock.sleep))
    assert 1.0 <= waited < 5.0


def test_cap_bounds_the_wait():
    clock = Clock()
    waited = run(rt.await_playback_idle(5.0, waiting=lambda: 3, now=clock.now, sleep=clock.sleep))
    assert 5.0 <= waited < 5.5


def test_default_reads_engine_playback_waiting(monkeypatch):
    monkeypatch.setattr(tts_engine, "_playback_waiting", 0)
    assert run(rt.await_playback_idle(0.1)) < 0.05


class FakeEngine:
    def __init__(self):
        self.calls = []

    def synthesize_cached(self, text, voice, speed):
        self.calls.append(text)
        return np.zeros(10, dtype=np.float32), speed


def test_render_synthesis_is_one_sentence_per_pool_task(monkeypatch):
    submitted = []

    class SpyPool:
        def submit(self, fn, *a, **k):
            submitted.append((fn, a))
            from concurrent.futures import Future
            f = Future()
            f.set_result(fn(*a, **k))
            return f

    monkeypatch.setattr(tts_engine, "_synthesis_pool", SpyPool())
    monkeypatch.setattr(tts_engine, "_playback_waiting", 0)
    eng = FakeEngine()
    for s in ["One.", "Two.", "Three."]:
        audio, sp = run(tts_engine.synthesize_sentence_for_render(s, "af_heart", 1.0, engine=eng))
        assert audio is not None
    assert eng.calls == ["One.", "Two.", "Three."]
    assert len(submitted) == 3
    for fn, args in submitted:
        texts = [a for a in args if isinstance(a, (str, list, tuple))]
        assert not any(isinstance(a, (list, tuple)) for a in args)
        assert args[0] in {"One.", "Two.", "Three."}


def test_render_synthesis_waits_for_throttle(monkeypatch):
    order = []

    async def fake_idle(cap_s=5.0, **kw):
        order.append("idle")
        return 0.0

    monkeypatch.setattr(rt, "await_playback_idle", fake_idle)
    eng = FakeEngine()
    orig = eng.synthesize_cached
    eng.synthesize_cached = lambda *a: (order.append("synth"), orig(*a))[1]
    run(tts_engine.synthesize_sentence_for_render("Hi.", "v", 1.0, engine=eng))
    assert order == ["idle", "synth"]


def test_modal_batch_allowed_predicate():
    clock = Clock()
    tr = rt.TaskTracker(now=clock.now)
    assert rt.modal_batch_allowed(waiting=lambda: 0, tracker=tr)
    assert not rt.modal_batch_allowed(waiting=lambda: 1, tracker=tr)
    tok = tr.start()
    clock.t = 9.0
    assert rt.modal_batch_allowed(waiting=lambda: 0, tracker=tr)
    clock.t = 10.5
    assert not rt.modal_batch_allowed(waiting=lambda: 0, tracker=tr)
    tr.finish(tok)
    assert rt.modal_batch_allowed(waiting=lambda: 0, tracker=tr)


def test_preview_served_while_render_pool_saturated(monkeypatch):
    render_pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(tts_engine, "_synthesis_pool", render_pool)
    gate = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        gate.wait(5)

    blockers = [render_pool.submit(slow) for _ in range(3)]
    assert started.wait(1)
    assert rt.preview_pool is not render_pool
    assert rt.preview_pool._max_workers == 1

    t0 = time.monotonic()
    out = run(rt.run_preview(lambda: "wav"))
    assert out == "wav"
    assert time.monotonic() - t0 < 1.0
    assert not blockers[1].done()
    gate.set()
    for b in blockers:
        b.result(2)
    render_pool.shutdown()


def test_preview_endpoint_uses_preview_pool(client, monkeypatch):
    seen = []
    orig = rt.preview_pool.submit

    def spy(fn, *a, **k):
        seen.append(threading.current_thread().name)
        return orig(fn, *a, **k)

    monkeypatch.setattr(rt.preview_pool, "submit", spy)
    r = client.get("/voices/preview/af_heart")
    assert r.status_code == 200
    assert seen
