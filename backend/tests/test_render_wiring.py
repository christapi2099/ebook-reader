"""Render wiring: engine tracking, Piper native timings, engine family, stats, upload voice."""
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.database as _db  # noqa: E402
import storage  # noqa: E402
from services import engine_manager, piper_engine, render_jobs as rj  # noqa: E402
from services import render_stats, render_triggers, render_worker as rw, tts_engine  # noqa: E402
from tests.test_render_worker import (  # noqa: E402,F401
    FakeAligner, FakeSynth, T0, TIERS, env, mk_book, book)


# ------------------------------------------------------------------ fakes
class FakeKokoroResult:
    def __init__(self, audio, tokens):
        self.audio, self.tokens = audio, tokens

    def __getitem__(self, i):
        return self.audio


class FakeKokoro:
    def __init__(self):
        self.calls = 0

    def __call__(self, text, voice="af_heart", speed=1.0):
        self.calls += 1
        a = np.full(2400, 0.1, dtype=np.float32)
        toks = [SimpleNamespace(text=w, phonemes="ab", start_ts=i * 0.05, end_ts=i * 0.05 + 0.04)
                for i, w in enumerate(text.split())]
        return [FakeKokoroResult(a, toks)]


def _pa(ph, n):
    return SimpleNamespace(phoneme=ph, num_samples=n)


class FakeChunk:
    def __init__(self, audio, pas):
        self.audio_float_array = audio
        self.phoneme_alignments = pas


class FakePiperVoice:
    def __init__(self, include_alignments):
        self.include_alignments = include_alignments
        self.config = SimpleNamespace(sample_rate=24000)
        self.calls = []

    def synthesize(self, text, syn_config=None, include_alignments=False):
        self.calls.append(include_alignments)
        pas = [_pa("^", 0), _pa("a", 100), _pa("b", 100), _pa(" ", 50),
               _pa("c", 200), _pa(",", 10), _pa("d", 100), _pa("$", 0)]
        return [FakeChunk(np.full(560, 0.2, dtype=np.float32), pas)]


@pytest.fixture
def piper(tmp_path, monkeypatch):
    d = tmp_path / "voices"
    d.mkdir()
    (d / "amy.onnx").write_bytes(b"model-amy")
    loads = []

    class FakePV:
        @staticmethod
        def load(path, use_cuda=False, include_alignments=False):
            loads.append(include_alignments)
            return FakePiperVoice(include_alignments)

    monkeypatch.setitem(sys.modules, "piper.voice", SimpleNamespace(PiperVoice=FakePV))
    monkeypatch.setitem(sys.modules, "piper.config",
                        SimpleNamespace(SynthesisConfig=lambda **k: SimpleNamespace(**k)))
    eng = piper_engine.PiperEngine(str(d))
    eng._loads = loads
    return eng


# ------------------------------------------------------------ 1. wiring
def test_apply_kokoro_sets_render_engine_and_synthesizes(monkeypatch):
    import main
    monkeypatch.setattr(tts_engine, "_render_engine", None)
    for r in (main.tts_router, main.voices_router, main.mp3_router):
        monkeypatch.setattr(r, "set_kokoro", lambda k: None)
    main._apply_kokoro(FakeKokoro())
    assert tts_engine._render_engine is not None
    res = rw.engine_synthesizer()("hello big world", "af_heart")
    assert len(res.audio) == 2400
    assert [t[0] for t in res.kokoro_tokens] == ["hello", "big", "world"]
    main._apply_kokoro(None)
    assert tts_engine._render_engine is None


def test_render_engine_follows_switch(monkeypatch):
    import main
    monkeypatch.setattr(tts_engine, "_render_engine", None)
    for r in (main.tts_router, main.voices_router, main.mp3_router):
        monkeypatch.setattr(r, "set_kokoro", lambda k: None)
    a, b = FakeKokoro(), FakeKokoro()
    main._apply_kokoro(a)
    main._apply_kokoro(b)
    rw.engine_synthesizer()("x y", "af_heart")
    assert (a.calls, b.calls) == (0, 1)


# ------------------------------------------------------- 2. piper native
def test_piper_render_uses_aligned_voice_and_native_words(piper):
    audio, words = piper.synthesize_for_render("ab c, d", "piper:amy")
    assert piper._loaded_align["amy"].calls == [True]
    assert True in piper._loads
    assert words == [(0.0, 200 / 24000), (250 / 24000, 450 / 24000), (460 / 24000, 560 / 24000)]
    assert len(audio) == 560


def test_engine_synthesizer_with_piper_returns_native_words(piper, monkeypatch):
    eng = tts_engine.TTSEngine(piper)
    res = rw.engine_synthesizer(engine=eng)("ab c, d", "piper:amy")
    assert len(res.native_words) == 3 and res.kokoro_tokens is None
    assert res.effective_speed == 1.0


def test_live_piper_path_unchanged(piper):
    out = piper("ab", voice="piper:amy")
    assert len(out[0][-1]) == 560
    assert piper._loaded["amy"].calls == [False]


# ------------------------------------------------------ 3. engine family
def _set_current(monkeypatch, obj):
    monkeypatch.setattr(engine_manager.manager, "current", lambda: obj)


def test_family_piper_vs_kokoro(piper, monkeypatch):
    _set_current(monkeypatch, piper)
    assert render_triggers.current_engine_family() == "piper"
    _set_current(monkeypatch, FakeKokoro())
    assert render_triggers.current_engine_family() == "kokoro"
    _set_current(monkeypatch, None)
    assert render_triggers.current_engine_family() == "kokoro"


def test_fingerprint_differs_across_families_same_across_devices(piper, monkeypatch):
    _set_current(monkeypatch, FakeKokoro())
    k1 = render_triggers.target_fingerprint_for("af_heart")
    _set_current(monkeypatch, object())  # another kokoro device/modal client
    assert render_triggers.target_fingerprint_for("af_heart") == k1
    _set_current(monkeypatch, piper)
    p = render_triggers.target_fingerprint_for("piper:amy")
    assert p != k1
    (Path(piper._dir) / "amy.onnx").write_bytes(b"other-model")
    assert render_triggers.target_fingerprint_for("piper:amy") != p  # .onnx hash is an input
    # a stale kokoro voice falls back to the piper default, same as synthesis does
    assert render_triggers.target_fingerprint_for("af_heart") == \
        render_triggers.target_fingerprint_for("piper:amy")


def test_family_switch_retargets_books(env, piper, monkeypatch):
    _set_current(monkeypatch, FakeKokoro())
    monkeypatch.setattr(_db, "engine", env)
    fp = render_triggers.target_fingerprint_for(None)
    mk_book(env, "b1", target=fp, job_status="idle", ready_generation=1, ready_fingerprint=fp)
    assert render_triggers.retarget_all() == []
    _set_current(monkeypatch, piper)
    assert render_triggers.retarget_all() == ["b1"]


# --------------------------------------------------------------- 4. stats
def test_render_book_records_stats(env, real_ffmpeg):
    mk_book(env, n=12)
    got = rj.claim_next("w1", T0 + timedelta(days=1), debounce_s=0)
    assert got == "b1"
    assert rw.render_book("b1", "w1", synthesizer=FakeSynth(), aligner=FakeAligner(),
                          tiers=TIERS) == rw.READY
    st = render_stats.read("b1")
    assert st["sentences_total"] == 12 and st["sentences_done"] == 12
    assert {"extract", "synthesize", "encode", "publish"} <= set(st["phase_timings"])


def test_render_error_recorded_in_stats(env, real_ffmpeg):
    mk_book(env, n=3)
    rj.claim_next("w1", T0 + timedelta(days=1), debounce_s=0)

    def boom(text, voice):
        raise RuntimeError("kaboom")
    assert rw.render_book("b1", "w1", synthesizer=boom, aligner=FakeAligner(),
                          tiers=TIERS, retries=1, sleep=lambda s: None) == rw.ERROR
    assert "kaboom" in render_stats.read("b1")["last_error"]


# --------------------------------------------------------------- 5. upload
def test_upload_voice_field_and_default(client, db_engine, sample_pdf_path):
    from sqlmodel import Session
    from db.models import Book
    data = sample_pdf_path.read_bytes()
    r = client.post("/documents/upload", data={"voice": "bf_emma"},
                    files={"file": ("a.pdf", data, "application/pdf")})
    assert r.status_code == 200, r.text
    with Session(db_engine) as s:
        b = s.get(Book, r.json()["book_id"])
        assert b.voice == "bf_emma"
        assert b.target_fingerprint == render_triggers.target_fingerprint_for("bf_emma")
        assert b.target_fingerprint != render_triggers.target_fingerprint_for(None)


def test_upload_without_voice_defaults_af_heart(client, db_engine, sample_pdf_path):
    from sqlmodel import Session
    from db.models import Book
    r = client.post("/documents/upload",
                    files={"file": ("b.pdf", sample_pdf_path.read_bytes() + b"\n%x", "application/pdf")})
    assert r.status_code == 200, r.text
    with Session(db_engine) as s:
        b = s.get(Book, r.json()["book_id"])
        assert b.voice is None
        assert b.target_fingerprint == render_triggers.target_fingerprint_for("af_heart")
