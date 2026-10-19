"""Piper TTS adapter — a lightweight local engine for low-power CPU/GPU hardware.

Piper (https://github.com/rhasspy/piper) is a small VITS-family model that
runs comfortably on a CPU or a modest GPU, unlike Kokoro-82M which needs real
GPU headroom to stay ahead of real-time at 2x+ playback speed. This module
wraps ``piper-tts`` so it satisfies the same callable contract every other
engine already satisfies (see ``services/engine_manager.py`` and
``services/tts_engine.py::_collect_result``):

    engine(text, voice=voice, speed=speed) -> iterable[result]
    result[-1] -> np.ndarray of mono float32 audio at SAMPLE_RATE (24000)
    result.tokens -> optional; Piper has no phoneme-alignment output, so this
                      is always [] and callers fall back to the existing
                      phoneme-proportional timestamp estimate.

Piper's native sample rate (22050 Hz for most released voices, read from the
voice's own config rather than assumed) does not match the rest of the
pipeline's hardcoded ``SAMPLE_RATE = 24000``, so every synthesis result is
resampled here before it ever reaches ``tts_engine.py``.
"""

from __future__ import annotations

import logging
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Kept in sync with services/tts_engine.py's SAMPLE_RATE by hand: that module
# owns the constant (every WAV chunk, cache duration, and silence gap in the
# app assumes it), so this adapter resamples to it rather than the reverse.
TARGET_SAMPLE_RATE = 24000


class PiperResult:
    """A single-chunk result shaped like Kokoro's ``KPipeline`` yield.

    Only ``result[-1]`` (the audio) and ``result.tokens`` (word timing) are
    ever read by callers (``tts_engine.py::_collect_result``), so this is
    deliberately not a full NamedTuple mirror of Kokoro's result type.
    """

    __slots__ = ("tokens", "_audio")

    def __init__(self, audio: np.ndarray) -> None:
        self.tokens: list[Any] = []
        self._audio = audio

    def __getitem__(self, index: int) -> np.ndarray:
        return self._audio


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return _prevent_clipping(audio)
    from scipy.signal import resample_poly

    ratio = Fraction(target_rate, source_rate).limit_denominator(1000)
    resampled = resample_poly(audio, ratio.numerator, ratio.denominator)
    return _prevent_clipping(resampled.astype(np.float32, copy=False))


def _prevent_clipping(audio: np.ndarray, ceiling: float = 0.95) -> np.ndarray:
    """Scale down if resampling/normalization pushed any sample past `ceiling`.

    Piper's own volume normalization already targets close to full scale, and
    ``resample_poly`` can overshoot a signal's original peak near sharp
    transients (Gibbs ringing). Left unchecked this clips at 24kHz output,
    which is audible as crackling/distortion easily mistaken for the engine
    "struggling" rather than a level problem.
    """
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > ceiling:
        audio = audio * (ceiling / peak)
    return audio


_PUNCT = set(",.;:!?\u2014\u2013-\"'()\u201c\u201d\u2018\u2019\u2026")


def words_from_alignments(chunks, rate: int) -> list[tuple[float, float]]:
    """Per-word (start_s, end_s) from phoneme durations: runs of phonemes between
    spaces/punctuation, timed by the cumulative ``num_samples`` (same extraction
    as scripts/spike_chapter.py)."""
    t, words, cur_s, cur_e = 0, [], None, None
    for ch in chunks:
        for pa in getattr(ch, "phoneme_alignments", None) or []:
            n = int(pa.num_samples)
            ph = pa.phoneme
            if ph in ("^", "$"):
                pass
            elif ph == " " or ph in _PUNCT:
                if cur_s is not None:
                    words.append((cur_s / rate, cur_e / rate))
                    cur_s = cur_e = None
            else:
                if cur_s is None:
                    cur_s = t
                cur_e = t + n
            t += n
    if cur_s is not None:
        words.append((cur_s / rate, cur_e / rate))
    return words


def probe_voices(voices_dir) -> tuple[bool, str | None]:
    """``(usable, reason)``: needs the ``piper`` package and at least one voice model.

    Checks with ``find_spec`` so probing never imports the ONNX runtime.
    """
    from importlib.util import find_spec

    if find_spec("piper") is None:
        return False, "piper-tts is not installed"
    voices = Path(voices_dir)
    if not voices.is_dir() or not any(voices.glob("*.onnx")):
        return False, f"no Piper voice models in {voices}"
    return True, None


def is_piper(obj: Any) -> bool:
    return isinstance(obj, PiperEngine)


_patched_lock = threading.Lock()
_patched: dict[tuple[str, int, int], Any] = {}


def _alignment_model(model_path: Path) -> Any:
    """The voice model patched in memory to expose phoneme alignments (what
    ``PiperVoice.load(include_alignments=True)`` does), computed ONCE per file and shared
    by every worker: the protobuf parse/patch/serialise holds the GIL for ~2 s, so N
    workers doing it each would serialise into N x 2 s of start-up."""
    st = model_path.stat()
    key = (str(model_path), st.st_mtime_ns, st.st_size)
    with _patched_lock:
        if key not in _patched:
            try:
                import onnx
                from piper.patch_voice_with_alignment import add_alignment_output

                proto = onnx.load(str(model_path))
                add_alignment_output(proto)
                _patched.clear()  # keep one patched model (tens of MB) per process
                _patched[key] = proto.SerializeToString()
            except (ImportError, ValueError):
                _patched[key] = str(model_path)  # unpatched: same fallback as PiperVoice.load
        return _patched[key]


def _capped_session(model_path: Path, include_alignments: bool, threads: int):
    """A new CPU InferenceSession for ``voice`` limited to ``threads`` intra-op threads."""
    import onnxruntime

    model: Any = _alignment_model(model_path) if include_alignments else str(model_path)
    opts = onnxruntime.SessionOptions()
    opts.intra_op_num_threads = max(1, int(threads))
    opts.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(model, sess_options=opts,
                                        providers=["CPUExecutionProvider"])


def _strip_prefix(voice: str) -> str:
    return voice.split(":", 1)[1] if voice.startswith("piper:") else voice


class PiperEngine:
    """Serves every Piper voice found in ``voices_dir`` through one callable.

    Kokoro's ``KPipeline`` is one model serving many named voices, and the
    rest of the app (voice picker, WS ``play``/``prefetch_speed`` messages)
    is built around passing a ``voice=`` string per call rather than
    rebuilding the engine when the voice changes. Piper voices are each their
    own ``.onnx`` model, so this class mirrors that shape by lazily loading
    and caching one ``PiperVoice`` per model file, keyed by filename stem, and
    picking the right one per call from the incoming ``voice`` id (accepts
    both ``"piper:<name>"`` and a bare ``"<name>"``).
    """

    # Safe to run several instances (one per thread) side by side: every instance owns
    # its voices/sessions and nothing else is shared except Piper's own locked espeak
    # phonemizer. The render worker reads this to decide whether it may parallelise.
    parallel_safe = True

    def __init__(self, voices_dir: str, device: str = "cpu", *, warm: bool = True,
                 intra_op_threads: int | None = None) -> None:
        self._dir = Path(voices_dir)
        self._device = device
        self._use_cuda = device == "gpu"
        # ``None`` keeps PiperVoice.load's defaults (onnxruntime picks all cores).
        self._threads = intra_op_threads
        # Voice caches are check-then-set; the lock makes concurrent first use load once.
        self._load_lock = threading.RLock()
        self._loaded: dict[str, Any] = {}
        self._source_rates: dict[str, int] = {}
        self._loaded_align: dict[str, Any] = {}

        if not self._dir.exists() or not any(self._dir.glob("*.onnx")):
            raise FileNotFoundError(
                f"No Piper voice models (*.onnx) found in {self._dir}"
            )

        # Building the ONNX inference session (PiperVoice.load) takes multiple
        # seconds. Left lazy, that cost lands on the first real synthesis call,
        # which happens mid-playback right after an engine switch or restart --
        # a multi-second stall far past the frontend's ~0.3s startup buffer.
        # Warm the default voice now, while the engine build is still gated
        # behind the "switching" state in the UI.
        if warm:
            try:
                self._load(self.default_voice())
            except Exception:
                logger.warning("[piper] eager warm-up of default voice failed", exc_info=True)

    def clone_for_worker(self, intra_op_threads: int | None = None) -> "PiperEngine":
        """An independent engine over the same voices for ONE render thread: its own
        sessions (loaded lazily, no warm-up) capped to ``intra_op_threads``."""
        return PiperEngine(str(self._dir), device=self._device, warm=False,
                           intra_op_threads=intra_op_threads)

    def _load_voice(self, model_path: Path, include_alignments: bool):
        from piper.voice import PiperVoice

        if self._threads is None or self._use_cuda:
            if include_alignments:
                return PiperVoice.load(str(model_path), use_cuda=self._use_cuda,
                                       include_alignments=True)
            return PiperVoice.load(str(model_path), use_cuda=self._use_cuda)
        # PiperVoice.load hardcodes onnxruntime.SessionOptions(), so N sessions would each
        # spawn one thread per core. Build the voice the way load() does (config + session +
        # espeak data) but with a capped session.
        import json

        from piper.config import PiperConfig
        from piper.phonemize_espeak import ESPEAK_DATA_DIR

        with open(f"{model_path}.json", "r", encoding="utf-8") as fh:
            config = PiperConfig.from_dict(json.load(fh))
        return PiperVoice(config=config,
                          session=_capped_session(model_path, include_alignments, self._threads),
                          espeak_data_dir=Path(ESPEAK_DATA_DIR), download_dir=Path.cwd())

    def _load(self, name: str):
        cached = self._loaded.get(name)
        if cached is not None:
            return cached
        with self._load_lock:
            cached = self._loaded.get(name)
            if cached is not None:
                return cached
            model_path = self._dir / f"{name}.onnx"
            if not model_path.exists():
                raise FileNotFoundError(f"Piper voice model not found: {model_path}")

            logger.info("[piper] loading voice %s (cuda=%s)", name, self._use_cuda)
            voice = self._load_voice(model_path, False)
            self._source_rates[name] = int(getattr(voice.config, "sample_rate", 22050))
            self._loaded[name] = voice  # publish last: readers see a complete voice
            return voice

    def _load_aligned(self, name: str):
        """Voice loaded with ``include_alignments=True`` (render path only; the live
        path keeps its plain voice so playback is unaffected)."""
        cached = self._loaded_align.get(name)
        if cached is not None:
            return cached
        with self._load_lock:
            cached = self._loaded_align.get(name)
            if cached is not None:
                return cached
            model_path = self._dir / f"{name}.onnx"
            if not model_path.exists():
                raise FileNotFoundError(f"Piper voice model not found: {model_path}")
            voice = self._load_voice(model_path, True)
            self._source_rates[name] = int(getattr(voice.config, "sample_rate", 22050))
            self._loaded_align[name] = voice
            return voice

    def synthesize_for_render(self, text: str, voice: str = "") -> tuple[np.ndarray, list]:
        """1.0x render synthesis: ``(audio at 24 kHz, native word (start_s, end_s))``
        from ONE ``synthesize(..., include_alignments=True)`` call."""
        from piper.config import SynthesisConfig

        name = _strip_prefix(voice) if voice else self.default_voice()
        try:
            v = self._load_aligned(name)
        except FileNotFoundError:
            logger.warning("[piper] unknown voice %r, falling back to default", name)
            name = self.default_voice()
            v = self._load_aligned(name)
        rate = self._source_rates[name]
        chunks = list(v.synthesize(text, syn_config=SynthesisConfig(length_scale=1.0),
                                   include_alignments=True))
        if not chunks:
            return np.zeros(0, dtype=np.float32), []
        native = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32, copy=False)
        words = words_from_alignments(chunks, rate)
        return _resample(native, rate, TARGET_SAMPLE_RATE), words

    def __call__(self, text: str, voice: str = "", speed: float = 1.0) -> list[PiperResult]:
        from piper.config import SynthesisConfig

        name = _strip_prefix(voice) if voice else self.default_voice()
        try:
            piper_voice = self._load(name)
        except FileNotFoundError:
            # `voice` came from the client, and voice selection isn't tied to
            # the active engine (settings.ts stores them independently) -- a
            # stale Kokoro voice id can reach here right after an engine
            # switch, before the frontend has picked a valid Piper voice. Fall
            # back to the default voice rather than dropping the sentence.
            logger.warning("[piper] unknown voice %r, falling back to default", name)
            name = self.default_voice()
            piper_voice = self._load(name)
        length_scale = 1.0 / speed if speed > 0 else 1.0
        syn_config = SynthesisConfig(length_scale=length_scale)

        parts = [chunk.audio_float_array for chunk in piper_voice.synthesize(text, syn_config=syn_config)]
        if not parts:
            return [PiperResult(np.zeros(0, dtype=np.float32))]

        audio = np.concatenate(parts).astype(np.float32, copy=False)
        audio = _resample(audio, self._source_rates[name], TARGET_SAMPLE_RATE)
        return [PiperResult(audio)]

    def default_voice(self) -> str:
        return sorted(p.stem for p in self._dir.glob("*.onnx"))[0]

    def available_voices(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.onnx"))
