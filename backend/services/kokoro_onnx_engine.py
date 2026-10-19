"""Kokoro-82M through ONNX Runtime (fp32 ``kokoro-v1.0.onnx``, ~325 MB) as its own engine.

The torch ``KPipeline`` and this engine speak the same language (same Kokoro voices, same
misaki G2P, 24 kHz mono float32) but are different numerical pipelines, so the render
fingerprint treats ``kokoro-onnx`` as its own engine family (see ``render_triggers``).

Contract, identical to ``PiperEngine`` so ``TTSEngine`` and the render worker need no special
case:

    engine(text, voice=voice, speed=speed) -> [result]     result[-1] -> float32 audio
    engine.synthesize_for_render(text, voice) -> (audio, None)
    engine.parallel_safe / clone_for_worker(threads)

The ONNX graph returns audio only (no per-phoneme durations), so ``synthesize_for_render``
reports no native word timings and the aligner falls through to its CTC pass over the audio,
the same path kokoro-cpu uses when it yields no usable tokens.

Model files live in ``backend/voices/kokoro-onnx/`` (gitignored):
``kokoro-v1.0.onnx`` and ``voices-v1.0.bin`` (an ``.npz`` of ``(510, 1, 256)`` style packs).
Inference needs only ``onnxruntime`` and ``misaki`` (already dependencies); the
``kokoro-onnx`` PyPI package is deliberately not required.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
MAX_PHONEMES = 510
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"
RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
DOWNLOADS = ((MODEL_FILE, f"{RELEASE_URL}/{MODEL_FILE}", 300_000_000),
             (VOICES_FILE, f"{RELEASE_URL}/{VOICES_FILE}", 20_000_000))
DEFAULT_VOICE = "af_heart"
NOT_INSTALLED = "Kokoro ONNX model not installed"
GPU_PROVIDERS = ("CUDAExecutionProvider", "ROCMExecutionProvider")

_VOCAB_PATH = Path(__file__).with_name("kokoro_vocab.json")


class KokoroOnnxResult:
    """Shaped like Piper's result: ``result[-1]`` is the audio, ``tokens`` is always empty."""

    __slots__ = ("tokens", "_audio")

    def __init__(self, audio: np.ndarray) -> None:
        self.tokens: list[Any] = []
        self._audio = audio

    def __getitem__(self, index: int) -> np.ndarray:
        return self._audio


# ------------------------------------------------------------------ install / probe
def probe(model_dir) -> tuple[bool, str | None]:
    """``(usable, reason)``: needs onnxruntime + misaki and both model files. Uses
    ``find_spec`` so probing never imports the runtime."""
    from importlib.util import find_spec

    for pkg in ("onnxruntime", "misaki"):
        if find_spec(pkg) is None:
            return False, f"{pkg} is not installed"
    d = Path(model_dir)
    missing = [n for n, _, _ in DOWNLOADS if not (d / n).is_file()]
    if missing:
        return False, f"{NOT_INSTALLED} ({', '.join(missing)} missing)"
    return True, None


def is_installed(model_dir) -> bool:
    return all((Path(model_dir) / n).is_file() for n, _, _ in DOWNLOADS)


def install(model_dir, *, urls=DOWNLOADS, progress: Callable[[str, int, int], None] | None = None,
            opener=urllib.request.urlopen) -> Path:
    """Download the model files into ``model_dir`` (atomic: ``.part`` then rename).

    Files that already exist with a plausible size are kept. ``urls`` is
    ``((filename, url, min_bytes), ...)`` so tests can inject small fixtures."""
    d = Path(model_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name, url, min_bytes in urls:
        dest = d / name
        if dest.is_file() and dest.stat().st_size >= min_bytes:
            continue
        part = dest.with_name(dest.name + ".part")
        with opener(url) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(name, done, total)
        if part.stat().st_size < min_bytes:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"download of {name} looks truncated")
        os.replace(part, dest)
    return d


# ------------------------------------------------------------------ phonemes
def load_vocab() -> dict[str, int]:
    with open(_VOCAB_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _tokens_to_ps(tokens) -> str:
    return "".join(t.phonemes + (" " if t.whitespace else "") for t in tokens).strip()


def _waterfall_last(tokens, next_count: int) -> int:
    """Where to cut an over-long token run: prefer sentence, then clause, then comma ends
    (the same preference order as ``kokoro.KPipeline.waterfall_last``)."""
    for w in ("!.?…", ":;", ",—"):
        z = next((i for i, t in reversed(list(enumerate(tokens))) if t.phonemes in set(w)), None)
        if z is None:
            continue
        z += 1
        if z < len(tokens) and tokens[z].phonemes in (")", "”"):
            z += 1
        if next_count - len(_tokens_to_ps(tokens[:z])) <= MAX_PHONEMES:
            return z
    return len(tokens)


def chunk_phonemes(tokens) -> list[str]:
    """Phoneme strings of at most 510 symbols, split on the same boundaries as torch Kokoro."""
    out: list[str] = []
    tks: list = []
    pcount = 0
    for t in tokens:
        t.phonemes = "" if t.phonemes is None else t.phonemes
        next_ps = t.phonemes + (" " if t.whitespace else "")
        next_pcount = pcount + len(next_ps.rstrip())
        if next_pcount > MAX_PHONEMES and tks:
            z = _waterfall_last(tks, next_pcount)
            out.append(_tokens_to_ps(tks[:z]))
            tks = tks[z:]
            pcount = len(_tokens_to_ps(tks))
            if not tks:
                next_ps = next_ps.lstrip()
        tks.append(t)
        pcount += len(next_ps)
    if tks:
        out.append(_tokens_to_ps(tks))
    return [p[:MAX_PHONEMES] for p in out if p]


_g2p_lock = threading.Lock()
_g2p: Any = None


def _default_g2p():
    """One process-wide American-English misaki G2P (what ``KPipeline(lang_code='a')`` builds)."""
    global _g2p
    with _g2p_lock:
        if _g2p is None:
            from misaki import en, espeak

            try:
                fallback = espeak.EspeakFallback(british=False)
            except Exception:
                logger.warning("[kokoro-onnx] espeak fallback unavailable: OOD words are skipped")
                fallback = None
            _g2p = en.G2P(trf=False, british=False, fallback=fallback, unk="")
        return _g2p


# ------------------------------------------------------------------ session
def pick_providers(use_gpu: bool, available: list[str] | None = None) -> list[str]:
    """CPU unless a GPU was asked for AND onnxruntime offers a GPU provider. Never raises."""
    if available is None:
        try:
            import onnxruntime
            available = onnxruntime.get_available_providers()
        except Exception:
            available = []
    chosen = [p for p in GPU_PROVIDERS if use_gpu and p in available]
    return chosen + ["CPUExecutionProvider"]


def _make_session(model_path: Path, threads: int | None, providers: list[str]):
    import onnxruntime

    opts = onnxruntime.SessionOptions()
    if threads is not None:
        opts.intra_op_num_threads = max(1, int(threads))
        opts.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(str(model_path), sess_options=opts, providers=providers)


def default_threads() -> int | None:
    """``RENDER_THREADS_TOTAL`` when set (the render budget), else let ORT use every core."""
    raw = os.environ.get("RENDER_THREADS_TOTAL")
    try:
        return max(1, int(raw)) if raw else None
    except ValueError:
        return None


class KokoroOnnxEngine:
    """Kokoro-82M on ONNX Runtime; serves every voice in ``voices-v1.0.bin``."""

    def __init__(self, model_dir, device: str = "cpu", *, warm: bool = True,
                 intra_op_threads: int | None = None, session_factory=None,
                 g2p=None, _shared: dict | None = None) -> None:
        self._dir = Path(model_dir)
        self._device = device
        self._threads = intra_op_threads if intra_op_threads is not None else default_threads()
        self._providers = pick_providers(device == "gpu")
        self._session_factory = session_factory or _make_session
        self._g2p = g2p
        self._run_lock = threading.Lock()  # one run at a time per instance
        model_path = self._dir / MODEL_FILE
        voices_path = self._dir / VOICES_FILE
        if not model_path.is_file() or not voices_path.is_file():
            raise FileNotFoundError(f"Kokoro ONNX model files not found in {self._dir}")
        if _shared is not None:  # clones reuse the (read-only) voices and vocab
            self._voices, self._vocab = _shared["voices"], _shared["vocab"]
        else:
            with np.load(voices_path) as npz:
                self._voices = {k: np.asarray(npz[k], dtype=np.float32) for k in npz.files}
            self._vocab = load_vocab()
        self._model_path = model_path
        self._session: Any = None
        self._session_lock = threading.Lock()
        if warm:
            try:
                self._get_session()
            except Exception:
                logger.warning("[kokoro-onnx] eager session build failed", exc_info=True)

    # -- engine contract ------------------------------------------------
    @property
    def parallel_safe(self) -> bool:
        """Independent CPU sessions can run side by side; a GPU session is one device."""
        return self._providers == ["CPUExecutionProvider"]

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    def clone_for_worker(self, intra_op_threads: int | None = None) -> "KokoroOnnxEngine":
        """An independent engine for ONE render thread: own session (lazy), capped threads,
        shared voices/vocab/G2P."""
        return KokoroOnnxEngine(self._dir, device=self._device, warm=False,
                                intra_op_threads=intra_op_threads or 1,
                                session_factory=self._session_factory, g2p=self._g2p,
                                _shared={"voices": self._voices, "vocab": self._vocab})

    def default_voice(self) -> str:
        return DEFAULT_VOICE if DEFAULT_VOICE in self._voices else sorted(self._voices)[0]

    def available_voices(self) -> list[str]:
        return sorted(self._voices)

    # -- internals ------------------------------------------------------
    def _get_session(self):
        if self._session is None:
            with self._session_lock:
                if self._session is None:
                    logger.info("[kokoro-onnx] building session (providers=%s, threads=%s)",
                                self._providers, self._threads)
                    self._session = self._session_factory(
                        self._model_path, self._threads, self._providers)
        return self._session

    def _style(self, voice: str) -> np.ndarray:
        """Style pack for ``voice`` (``a,b`` averages packs, as torch Kokoro does)."""
        names = [v.strip() for v in (voice or "").split(",") if v.strip()]
        packs = [self._voices[n] for n in names if n in self._voices]
        if not packs or len(packs) != len(names):
            if voice:
                logger.warning("[kokoro-onnx] unknown voice %r, falling back to default", voice)
            packs = [self._voices[self.default_voice()]]
        return packs[0] if len(packs) == 1 else np.mean(np.stack(packs), axis=0)

    def _phonemes(self, text: str) -> list[str]:
        g2p = self._g2p or _default_g2p()
        with _g2p_lock:  # misaki's G2P (spaCy) is not documented thread-safe
            _, tokens = g2p(text)
        return chunk_phonemes(tokens)

    def _infer(self, ps: str, style_pack: np.ndarray, speed: float) -> np.ndarray:
        ids = [self._vocab[c] for c in ps if c in self._vocab]
        if not ids:
            return np.zeros(0, dtype=np.float32)
        ids = ids[:MAX_PHONEMES]
        style = style_pack[min(len(ps), style_pack.shape[0]) - 1]  # (1, 256), as torch Kokoro
        out = self._get_session().run(None, {
            "tokens": np.asarray([[0, *ids, 0]], dtype=np.int64),
            "style": np.ascontiguousarray(style, dtype=np.float32).reshape(1, -1),
            "speed": np.asarray([speed], dtype=np.float32),
        })[0]
        return np.asarray(out, dtype=np.float32).reshape(-1)

    def _synthesize(self, text: str, voice: str, speed: float) -> np.ndarray:
        name = voice.split(":", 1)[1] if voice.startswith("kokoro-onnx:") else voice
        style = self._style(name or self.default_voice())
        parts = [self._infer(ps, style, speed) for ps in self._phonemes(text)]
        parts = [p for p in parts if p.size]
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts).astype(np.float32, copy=False)

    # -- public synthesis -----------------------------------------------
    def synthesize_for_render(self, text: str, voice: str = "") -> tuple[np.ndarray, None]:
        """1.0x render synthesis: ``(audio at 24 kHz, None)``; word timings come from the
        CTC aligner over this audio."""
        return self._synthesize(text, voice, 1.0), None

    def __call__(self, text: str, voice: str = "", speed: float = 1.0) -> list[KokoroOnnxResult]:
        speed = speed if speed and speed > 0 else 1.0
        return [KokoroOnnxResult(self._synthesize(text, voice, float(speed)))]
