"""Per-sentence word timing: engine-native times, else CTC forced alignment of the
known text, else a syllable estimate. See docs/plans/process-first-architecture.md."""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Sequence

import logging

import numpy as np

from services.word_boxes import match_words

# Mean per-word posterior (0..1) a CTC alignment must reach. UNCALIBRATED: the Phase 1a spike did not tune it.
CTC_SCORE_GATE = 0.50
MIN_WORD_MS = 20           # one 20 ms CTC frame: minimal positive span
ALIGNER_MODEL_ID = "MMS-300m-1130 forced aligner (ONNX, deskpai/ctc_forced_aligner)"
_CTC_RATE = 16000
logger = logging.getLogger(__name__)

_STRIDE_MS = 20.0


@dataclass
class WordTiming:
    word: str
    start_ms: int
    end_ms: int
    timing_source: str  # 'kokoro' | 'piper' | 'aligned' | 'estimated'


class Aligner(Protocol):
    def align(self, audio: np.ndarray, sr: int, words: list[str]) -> list[tuple[float, float, float]]:
        """-> per word (start_ms, end_ms, confidence 0..1)."""


def count_syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 1
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def distribute_by_syllables(duration_ms: int, words: Sequence[str]) -> list[WordTiming]:
    """Contiguous spans from 0 to exactly duration_ms, weight max(1, syllables);
    the last word absorbs the rounding remainder."""
    if not words:
        return []
    wts = [max(1, count_syllables(w)) for w in words]
    total = sum(wts)
    out, cur = [], 0
    for i, (w, wt) in enumerate(zip(words, wts)):
        end = duration_ms if i == len(words) - 1 else cur + duration_ms * wt // total
        out.append(WordTiming(w, cur, end, "estimated"))
        cur = end
    return out


def _fix(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Monotonic, non-overlapping, every span at least MIN_WORD_MS long."""
    out, prev_end = [], 0
    for s, e in spans:
        s = max(s, prev_end)
        e = max(e, s + MIN_WORD_MS)
        out.append((s, e))
        prev_end = e
    return out


def _build(words, spans, source) -> list[WordTiming]:
    return [WordTiming(w, s, e, source) for w, (s, e) in zip(words, _fix(spans))]


class CtcOnnxAligner:
    """MMS CTC forced aligner. The ONNX session is called directly on the exact-length
    16 kHz sentence with a fixed 20 ms stride (the library's generate_emissions pads to
    30 s windows and ceil()s the stride). Lazy: nothing loads until first use."""

    def __init__(self, threads: int = 8, model_path: Optional[Path] = None):
        self.threads, self.model_path, self._sess, self._tok = threads, model_path, None, None
        self._load_lock = threading.Lock()

    def _load(self):
        with self._load_lock:
            if self._sess is None:
                self._load_locked()

    def _load_locked(self):
        import onnxruntime as ort
        import ctc_forced_aligner as c
        p = self.model_path or Path.home() / "ctc_forced_aligner" / "model.onnx"
        c.ensure_onnx_model(str(p), c.MODEL_URL)
        so = ort.SessionOptions()
        so.intra_op_num_threads = self.threads
        self._tok = c.Tokenizer()
        self._sess = ort.InferenceSession(str(p), so)  # published last: _sess gates use

    def align(self, audio, sr, words):
        import ctc_forced_aligner as c
        from math import gcd
        from scipy.signal import resample_poly
        if self._sess is None:
            self._load()
        x = np.asarray(audio, np.float32)
        if sr != _CTC_RATE:
            g = gcd(sr, _CTC_RATE)
            x = resample_poly(x, _CTC_RATE // g, sr // g).astype(np.float32)
        x = ((x - x.mean()) / (x.std() + 1e-7)).astype(np.float32)[None, :]
        logits = self._sess.run(["logits"], {"input_values": x})[0][0]
        mx = logits.max(-1, keepdims=True)
        lp = logits - mx - np.log(np.exp(logits - mx).sum(-1, keepdims=True))
        em = np.concatenate([lp, np.zeros((lp.shape[0], 1))], 1).astype(np.float32)
        norm = [re.sub(r"[^a-z']", "", c.text_normalize(w, "eng").lower()) for w in words]
        toks = [" ".join(ch for ch in n if ch in c.VOCAB_DICT) for n in norm]
        # CTC needs at least one frame per target. The library's native check aborts
        # the whole process instead of raising, so refuse here and let the caller
        # fall back to syllable timing. Each word adds a <star> plus its characters.
        targets = sum(len(t.split()) for t in toks) + 2 * len(words)
        if lp.shape[0] < targets:
            raise ValueError(f"audio too short for CTC: {lp.shape[0]} frames, {targets} targets")
        starred, text_starred = [], []
        for w, t in zip(words, toks):
            starred += ["<star>", t]
            text_starred += ["<star>", w]
        segments, scores, blank = c.get_alignments(em, starred, self._tok)
        spans = c.get_spans(starred, segments, blank)
        res = c.postprocess_results(text_starred, spans, _STRIDE_MS, scores)
        return [(r["start"] * 1000, r["end"] * 1000, float(math.exp(min(0.0, r["score"])))) for r in res]


_default: Optional[CtcOnnxAligner] = None


_default_lock = threading.Lock()


def default_aligner() -> CtcOnnxAligner:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = CtcOnnxAligner()
    return _default


def align_sentence(audio: np.ndarray, sr: int, text: str, *, native_words=None,
                   kokoro_tokens=None, aligner: Optional[Aligner] = None) -> list[WordTiming]:
    """native_words: Piper [(start_s, end_s)]; kokoro_tokens: [(text, start_s, end_s)]."""
    words = text.split()
    if not words:
        return []
    ms = lambda s: int(round(s * 1000))  # noqa: E731
    if kokoro_tokens:
        m = match_words(words, [t[0] for t in kokoro_tokens])
        if len(m) == len(words):
            return _build(words, [(ms(kokoro_tokens[m[i]][1]), ms(kokoro_tokens[m[i]][2]))
                                  for i in range(len(words))], "kokoro")
    if native_words and len(native_words) == len(words):
        return _build(words, [(ms(s), ms(e)) for s, e in native_words], "piper")
    duration_ms = int(round(len(audio) / sr * 1000))
    try:
        res = (aligner or default_aligner()).align(audio, sr, words)
    except ValueError:  # too little audio for the words: estimate instead
        logger.warning("[align] CTC skipped for %r", text[:60], exc_info=True)
        return distribute_by_syllables(duration_ms, words)
    if len(res) == len(words):
        score = sum(r[2] for r in res) / len(res)
        if score >= CTC_SCORE_GATE:
            return _build(words, [(int(round(r[0])), int(round(r[1]))) for r in res], "aligned")
    return distribute_by_syllables(duration_ms, words)
