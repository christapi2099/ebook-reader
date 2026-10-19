"""Kokoro ONNX engine: fake session (no model, no network), engine manager and family wiring."""
import io
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import engine_manager, kokoro_onnx_engine as ko, render_triggers  # noqa: E402
from services.engine_manager import KOKORO_ONNX, EngineManager  # noqa: E402


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, _outputs, feeds):
        self.calls.append(feeds)
        n = int(feeds["tokens"].shape[1])
        return [np.full(n * 100, 0.1 * float(feeds["speed"][0]), dtype=np.float32)]


class FakeG2P:
    """Each word becomes a token whose phonemes are its letters."""

    def __call__(self, text):
        words = text.split()
        return "", [SimpleNamespace(phonemes=w.lower(), whitespace=" " if i < len(words) - 1 else "")
                    for i, w in enumerate(words)]


def make_dir(path: Path, voices=("af_heart", "am_adam")) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ko.MODEL_FILE).write_bytes(b"fake-onnx")
    buf = io.BytesIO()
    rng = np.random.default_rng(0)
    np.savez(buf, **{v: rng.standard_normal((510, 1, 256)).astype(np.float32) for v in voices})
    (path / ko.VOICES_FILE).write_bytes(buf.getvalue())
    return path


@pytest.fixture
def engine(tmp_path):
    sessions = []

    def factory(model_path, threads, providers):
        s = FakeSession()
        s.threads, s.providers = threads, providers
        sessions.append(s)
        return s

    eng = ko.KokoroOnnxEngine(make_dir(tmp_path / "m"), warm=False, session_factory=factory,
                              g2p=FakeG2P())
    eng.sessions = sessions
    return eng


# ---------------------------------------------------------------- engine
def test_render_synthesis_has_no_native_words_and_uses_speed_one(engine):
    audio, words = engine.synthesize_for_render("hello world", "af_heart")
    assert words is None  # word timings come from the CTC aligner
    assert audio.dtype == np.float32 and audio.size > 0
    feeds = engine.sessions[0].calls[0]
    assert feeds["speed"].tolist() == [1.0]
    assert feeds["tokens"][0, 0] == 0 and feeds["tokens"][0, -1] == 0  # padded
    assert feeds["style"].shape == (1, 256)


def test_call_contract_matches_kokoro(engine):
    out = engine("hello", voice="am_adam", speed=1.5)
    assert out[0][-1].dtype == np.float32 and out[0].tokens == []
    assert engine.sessions[0].calls[0]["speed"].tolist() == [1.5]


def test_unknown_voice_falls_back_to_default(engine):
    engine("hello", voice="custom:mine")
    engine("hello", voice="af_heart")
    a, b = (c["style"] for c in engine.sessions[0].calls)
    np.testing.assert_array_equal(a, b)
    assert engine.default_voice() == "af_heart"
    assert engine.available_voices() == ["af_heart", "am_adam"]


def test_style_index_follows_phoneme_length(engine):
    engine("abc", voice="af_heart")
    style = engine.sessions[0].calls[0]["style"]
    np.testing.assert_array_equal(style[0], engine._voices["af_heart"][len("abc") - 1][0])


def test_empty_text_gives_empty_audio(engine):
    audio, _ = engine.synthesize_for_render("", "af_heart")
    assert audio.size == 0


def test_long_text_is_chunked_under_510_phonemes(engine):
    text = " ".join(["word" + "a" * 20] * 60)
    audio, _ = engine.synthesize_for_render(text, "af_heart")
    sizes = [c["tokens"].shape[1] - 2 for c in engine.sessions[0].calls]
    assert len(sizes) > 1 and max(sizes) <= ko.MAX_PHONEMES
    assert audio.size == sum((n + 2) * 100 for n in sizes)


def test_clone_has_own_session_capped_threads_and_shares_voices(engine):
    engine.synthesize_for_render("hi", "af_heart")
    clone = engine.clone_for_worker(3)
    assert clone is not engine and clone._voices is engine._voices
    clone.synthesize_for_render("hi", "af_heart")
    assert len(engine.sessions) == 2 and engine.sessions[1].threads == 3
    assert engine.parallel_safe and clone.parallel_safe


def test_clones_run_in_parallel_threads(engine):
    out, errs = [], []

    def work():
        try:
            out.append(engine.clone_for_worker(1).synthesize_for_render("a b c", "af_heart")[0].size)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(set(out)) == 1 and len(out) == 4


def test_render_threads_total_budget_default(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_THREADS_TOTAL", "6")
    eng = ko.KokoroOnnxEngine(make_dir(tmp_path / "m"), warm=False, session_factory=lambda *a: None)
    assert eng._threads == 6
    monkeypatch.setenv("RENDER_THREADS_TOTAL", "junk")
    assert ko.default_threads() is None


def test_missing_files_raise(tmp_path):
    with pytest.raises(FileNotFoundError):
        ko.KokoroOnnxEngine(tmp_path, warm=False)


def test_provider_selection_never_fails():
    assert ko.pick_providers(False, ["CUDAExecutionProvider", "CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert ko.pick_providers(True, ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert ko.pick_providers(True, ["ROCMExecutionProvider", "CPUExecutionProvider"]) == [
        "ROCMExecutionProvider", "CPUExecutionProvider"]


def test_gpu_provider_is_not_parallel_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(ko, "pick_providers", lambda use_gpu, available=None: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    eng = ko.KokoroOnnxEngine(make_dir(tmp_path / "m"), device="gpu", warm=False, session_factory=lambda *a: None)
    assert not eng.parallel_safe


def test_vocab_covers_misaki_phonemes():
    v = ko.load_vocab()
    assert v["ə"] and " " in v and len(v) > 100


# ------------------------------------------------------- probe / install
def test_probe_and_install(tmp_path):
    ok, reason = ko.probe(tmp_path / "none")
    assert not ok and reason.startswith(ko.NOT_INSTALLED)
    d = make_dir(tmp_path / "ok")
    assert ko.probe(d) == (True, None) and ko.is_installed(d)


class FakeResp:
    def __init__(self, data):
        self.data, self.headers = io.BytesIO(data), {"Content-Length": str(len(data))}

    def read(self, n):
        return self.data.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_install_downloads_atomically_and_skips_existing(tmp_path):
    seen, fetched = [], []

    def opener(url):
        fetched.append(url)
        return FakeResp(b"x" * 50)

    urls = (("a.bin", "http://h/a", 10), ("b.bin", "http://h/b", 10))
    ko.install(tmp_path / "d", urls=urls, opener=opener, progress=lambda *a: seen.append(a))
    assert (tmp_path / "d" / "a.bin").read_bytes() == b"x" * 50
    assert not list((tmp_path / "d").glob("*.part")) and seen
    ko.install(tmp_path / "d", urls=urls, opener=opener)
    assert len(fetched) == 2  # second call re-used the files


def test_install_rejects_truncated_download(tmp_path):
    with pytest.raises(RuntimeError, match="truncated"):
        ko.install(tmp_path, urls=(("a.bin", "u", 100),), opener=lambda u: FakeResp(b"short"))
    assert not list(tmp_path.iterdir())


# ----------------------------------------------------------- manager
def test_manager_option_reports_install_state(monkeypatch):
    m = EngineManager(kokoro_onnx_probe=lambda: (False, f"{ko.NOT_INSTALLED} (x missing)"),
                      torch_probe=lambda: {"torch_version": "2"}, piper_probe=lambda: (False, "n"),
                      remote_probe=lambda **k: {"configured": False})
    o = m.availability(KOKORO_ONNX)
    assert not o.available and o.installable and o.as_dict()["installable"] is True
    m2 = EngineManager(kokoro_onnx_probe=lambda: (False, "onnxruntime is not installed"))
    assert not m2.availability(KOKORO_ONNX).installable
    m3 = EngineManager(kokoro_onnx_probe=lambda: (True, None))
    assert m3.availability(KOKORO_ONNX).available and not m3.availability(KOKORO_ONNX).installable


def test_manager_switch_builds_and_uses_family(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_manager, "load_persisted_engine", lambda: None)
    monkeypatch.setattr(engine_manager, "save_persisted_engine", lambda e: None)
    eng = ko.KokoroOnnxEngine(make_dir(tmp_path / "m"), warm=False, session_factory=lambda *a: FakeSession(),
                              g2p=FakeG2P())
    m = EngineManager(kokoro_onnx_builder=lambda: (eng, None), kokoro_onnx_probe=lambda: (True, None))
    monkeypatch.setattr(engine_manager, "manager", m)
    m.switch(KOKORO_ONNX, persist=False)
    assert m.active() == KOKORO_ONNX
    assert render_triggers.current_engine_family() == "kokoro-onnx"
    k = render_triggers.target_fingerprint_for("af_heart")
    # device is not an input; unknown voice resolves to the default like synthesis does
    assert render_triggers.target_fingerprint_for("piper:zzz") == k
    assert render_triggers.target_fingerprint_for("am_adam") != k
    monkeypatch.setattr(m, "current", lambda: object())
    assert render_triggers.target_fingerprint_for("af_heart") != k  # torch kokoro family differs


def test_build_uses_gpu_only_when_selected(monkeypatch, tmp_path):
    d = make_dir(tmp_path / "m")
    seen = []
    orig = ko.KokoroOnnxEngine

    class Spy(orig):
        def __init__(self, *a, **kw):
            seen.append(kw.get("device"))
            super().__init__(*a, **{**kw, "warm": False})

    monkeypatch.setattr(ko, "KokoroOnnxEngine", Spy)
    monkeypatch.setattr(engine_manager, "load_persisted_gpu_device", lambda: None)
    eng, err = engine_manager.build_kokoro_onnx(d)
    assert err is None and seen == ["cpu"]
    monkeypatch.setattr(engine_manager, "load_persisted_gpu_device", lambda: "0")
    engine_manager.build_kokoro_onnx(d)
    assert seen[-1] == "gpu"
    eng, err = engine_manager.build_kokoro_onnx(tmp_path / "missing")
    assert eng is None and "FileNotFoundError" in err


def test_install_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routers import system

    app = FastAPI()
    app.include_router(system.router)

    monkeypatch.setattr(system.storage, "KOKORO_ONNX_DIR", tmp_path / "k")
    monkeypatch.setitem(system._install_state, "status", "idle")
    started = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            self.name = name

        def start(self):
            started.append(self.name)

    monkeypatch.setattr(system, "threading", SimpleNamespace(Thread=FakeThread, Lock=threading.Lock))
    c = TestClient(app)
    assert c.get("/api/system/engine/kokoro-onnx/install").json()["status"] == "idle"
    r = c.post("/api/system/engine/kokoro-onnx/install")
    assert r.status_code == 202 and r.json()["status"] == "running" and started
    assert c.post("/api/system/engine/kokoro-onnx/install").json()["status"] == "running"
    assert len(started) == 1  # idempotent while running
    system._install_state["status"] = "idle"
    make_dir(tmp_path / "k")
    assert c.post("/api/system/engine/kokoro-onnx/install").json()["status"] == "done"


@pytest.mark.skipif(not ko.is_installed(Path(os.environ.get("KOKORO_ONNX_DIR") or
                    Path(__file__).resolve().parent.parent / "voices" / "kokoro-onnx")),
                    reason="Kokoro ONNX model not installed")
def test_live_model_synthesizes():
    import storage
    eng = ko.KokoroOnnxEngine(storage.KOKORO_ONNX_DIR)
    audio, words = eng.synthesize_for_render("The quick brown fox.", "af_heart")
    assert words is None and 0.8 < audio.size / 24000 < 4.0 and float(np.abs(audio).max()) > 0.05
