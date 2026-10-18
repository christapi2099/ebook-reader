import numpy as np
import pytest

from services import aligner as al


class Fake:
    def __init__(self, res): self.res, self.calls = res, 0
    def align(self, audio, sr, words):
        self.calls += 1
        return self.res


AUD = np.zeros(24000, np.float32)  # 1000 ms


def test_kokoro_passthrough():
    r = al.align_sentence(AUD, 24000, "Hello world.", kokoro_tokens=[("Hello", 0.0, 0.4), ("world", 0.5, 0.9)],
                          aligner=Fake([]))
    assert [(w.start_ms, w.end_ms, w.timing_source) for w in r] == [(0, 400, "kokoro"), (500, 900, "kokoro")]


def test_piper_native_when_counts_equal():
    f = Fake([])
    r = al.align_sentence(AUD, 24000, "a big dog", native_words=[(0, .1), (.2, .5), (.6, .9)], aligner=f)
    assert {w.timing_source for w in r} == {"piper"} and f.calls == 0


def test_piper_count_mismatch_uses_ctc():
    f = Fake([(0, 400, .9), (500, 900, .8)])
    r = al.align_sentence(AUD, 24000, "Hello world", native_words=[(0, .4)], aligner=f)
    assert f.calls == 1 and {w.timing_source for w in r} == {"aligned"}


def test_gate_falls_back_to_estimate_exact_sum():
    f = Fake([(0, 400, .1), (500, 900, .2)])
    r = al.align_sentence(AUD, 24000, "Hello wonderful", aligner=f)
    assert {w.timing_source for w in r} == {"estimated"}
    assert r[0].start_ms == 0 and r[-1].end_ms == 1000
    assert sum(w.end_ms - w.start_ms for w in r) == 1000


def test_gate_boundary_passes():
    f = Fake([(0, 400, al.CTC_SCORE_GATE), (500, 900, al.CTC_SCORE_GATE)])
    assert al.align_sentence(AUD, 24000, "Hello world", aligner=f)[0].timing_source == "aligned"


def test_zero_length_words_get_positive_monotonic_span():
    f = Fake([(0, 0, .9), (0, 0, .9), (100, 100, .9), (300, 500, .9)])
    r = al.align_sentence(AUD, 24000, "I a x word", aligner=f)
    for w in r:
        assert w.end_ms > w.start_ms
    for a, b in zip(r, r[1:]):
        assert b.start_ms >= a.end_ms


@pytest.mark.parametrize("dur,words", [(1000, ["a", "extraordinary", "day"]), (7, ["x"] * 3), (12345, ["one", "two", "banana", "I"])])
def test_syllable_exact_sum(dur, words):
    r = al.distribute_by_syllables(dur, words)
    assert sum(w.end_ms - w.start_ms for w in r) == dur
    assert r[0].start_ms == 0 and r[-1].end_ms == dur


def test_syllable_weights():
    r = al.distribute_by_syllables(1000, ["a", "banana"])
    assert (r[1].end_ms - r[1].start_ms) > 2 * (r[0].end_ms - r[0].start_ms)


@pytest.mark.integration
def test_real_ctc_model():
    sf = pytest.importorskip("soundfile")
    from pathlib import Path
    if not (Path.home() / "ctc_forced_aligner" / "model.onnx").exists():
        pytest.skip("MMS ONNX model not downloaded")
    t = np.linspace(0, 1, 24000, dtype=np.float32)
    r = al.align_sentence(np.sin(2 * np.pi * 200 * t), 24000, "hello world")
    assert len(r) == 2


class _Session:
    """Stands in for the ONNX session: returns ``frames`` frames of 32 logits."""

    def __init__(self, frames):
        self.frames = frames

    def run(self, names, feed):
        return [np.zeros((1, self.frames, 32), np.float32)]


def test_ctc_refuses_audio_too_short_for_the_words_instead_of_aborting():
    """The library aborts the process (C++ terminate) when targets exceed frames,
    so the guard must raise before it is called."""
    a = al.CtcOnnxAligner()
    a._sess = _Session(frames=5)  # 100 ms of frames for a sentence of many words
    a._tok = None
    with pytest.raises(ValueError, match="too short"):
        a.align(np.zeros(1600, np.float32), 16000, "a long sentence with many words here".split())


def test_too_short_audio_falls_back_to_syllable_estimate():
    class Refuses:
        def align(self, audio, sr, words):
            raise ValueError("audio too short for CTC")

    words = "a long sentence with many words here".split()
    out = al.align_sentence(AUD, 24000, " ".join(words), aligner=Refuses())
    assert [w.word for w in out] == words
    assert {w.timing_source for w in out} == {"estimated"}
