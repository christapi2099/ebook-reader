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


def is_piper(obj: Any) -> bool:
    return isinstance(obj, PiperEngine)


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

    def __init__(self, voices_dir: str, device: str = "cpu") -> None:
        self._dir = Path(voices_dir)
        self._device = device
        self._use_cuda = device == "gpu"
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
        try:
            self._load(self.default_voice())
        except Exception:
            logger.warning("[piper] eager warm-up of default voice failed", exc_info=True)

    def _load(self, name: str):
        cached = self._loaded.get(name)
        if cached is not None:
            return cached

        from piper.voice import PiperVoice

        model_path = self._dir / f"{name}.onnx"
        if not model_path.exists():
            raise FileNotFoundError(f"Piper voice model not found: {model_path}")

        logger.info("[piper] loading voice %s (cuda=%s)", name, self._use_cuda)
        voice = PiperVoice.load(str(model_path), use_cuda=self._use_cuda)
        self._loaded[name] = voice
        self._source_rates[name] = int(getattr(voice.config, "sample_rate", 22050))
        return voice

    def _load_aligned(self, name: str):
        """Voice loaded with ``include_alignments=True`` (render path only; the live
        path keeps its plain voice so playback is unaffected)."""
        cached = self._loaded_align.get(name)
        if cached is not None:
            return cached
        from piper.voice import PiperVoice

        model_path = self._dir / f"{name}.onnx"
        if not model_path.exists():
            raise FileNotFoundError(f"Piper voice model not found: {model_path}")
        voice = PiperVoice.load(str(model_path), use_cuda=self._use_cuda,
                                include_alignments=True)
        self._loaded_align[name] = voice
        self._source_rates[name] = int(getattr(voice.config, "sample_rate", 22050))
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
