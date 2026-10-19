"""The render baseline harness: the comparator must catch what it exists to catch.

``scripts/render_baseline.py`` is the regression gate for the GPU/parallel-render work, so a
comparator that silently passes a broken render would be worse than none.
"""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import render_baseline as rb  # noqa: E402
from services import export_encoding  # noqa: E402


def _snap(engine="fake"):
    words = lambda a: [{"start_ms": a + i * 100, "end_ms": a + i * 100 + 90, "box": None,
                        "timing_source": "aligned"} for i in range(3)]
    seg = lambda i, a: {"index": i, "start_ms": a, "end_ms": a + 400, "gap_ms": 120,
                        "words": words(a)}
    return {
        "engine": engine, "corpus_sha256": "c", "verify": {"ok": True, "failures": []},
        "files": {"base.flac": {"sha256": "a", "pcm_sha256": "p", "bytes": 1000}},
        "manifest": {"voice": "v", "fingerprint": "f", "sample_rate": 24000, "gap_policy": "g",
                     "container": "ogg-opus", "base_effective_speed": 1.0,
                     "mixed_backends": False, "schema": 1, "duration_ms": 1000.0,
                     "tiers": {"1.0": {"requested_factor": 1.0,
                                       "segments": [seg(0, 0), seg(1, 520)]}}},
    }


def test_identical_snapshots_pass_in_both_modes():
    assert rb.diff_snapshots(_snap(), _snap(), exact=True).ok
    assert rb.diff_snapshots(_snap(), _snap(), exact=False).ok


def test_exact_mode_catches_a_pcm_change():
    fresh = _snap()
    fresh["files"]["base.flac"]["pcm_sha256"] = "different"
    assert not rb.diff_snapshots(_snap(), fresh, exact=True).ok
    assert rb.diff_snapshots(_snap(), fresh, exact=False).ok  # tolerated for real engines


def test_word_count_change_is_always_a_failure():
    fresh = copy.deepcopy(_snap())
    fresh["manifest"]["tiers"]["1.0"]["segments"][0]["words"].pop()
    rep = rb.diff_snapshots(_snap(), fresh, exact=False)
    assert any("word count" in f for f in rep.failures)


def test_sentence_order_change_is_a_failure():
    fresh = copy.deepcopy(_snap())
    fresh["manifest"]["tiers"]["1.0"]["segments"].reverse()
    assert any("index sequence" in f for f in rb.diff_snapshots(_snap(), fresh).failures)


def test_overlapping_and_escaping_words_are_caught():
    fresh = copy.deepcopy(_snap())
    words = fresh["manifest"]["tiers"]["1.0"]["segments"][0]["words"]
    words[1]["start_ms"] = words[0]["start_ms"] - 50  # starts before the previous word
    words[2]["end_ms"] = 9999                         # runs past its sentence
    msgs = " ".join(rb.diff_snapshots(_snap(), fresh).failures)
    assert "overlaps previous word" in msgs and "outside its sentence" in msgs


def test_duration_tolerance_is_per_engine():
    fresh = copy.deepcopy(_snap("piper"))
    for tier in fresh["manifest"]["tiers"].values():
        seg = tier["segments"][0]
        seg["end_ms"] += 80  # +20% on one sentence
        seg["words"][-1]["end_ms"] = seg["end_ms"]
    assert rb.diff_snapshots(_snap("piper"), fresh, engine="piper").ok
    assert not rb.diff_snapshots(_snap("kokoro-cpu"), fresh, engine="kokoro-cpu").ok


def test_total_duration_drift_is_caught():
    fresh = copy.deepcopy(_snap())
    fresh["manifest"]["duration_ms"] = 1100.0
    assert any("total duration" in f for f in rb.diff_snapshots(_snap(), fresh).failures)


def test_failed_verify_render_fails_the_comparison():
    fresh = _snap()
    fresh["verify"] = {"ok": False, "failures": ["boom"]}
    assert not rb.diff_snapshots(_snap(), fresh).ok


def test_corpus_mismatch_is_refused():
    fresh = _snap()
    fresh["corpus_sha256"] = "other"
    assert "corpus differs" in rb.diff_snapshots(_snap(), fresh).failures[0]


@pytest.mark.skipif(not export_encoding.ffmpeg_available(), reason="needs ffmpeg")
def test_fake_engine_renders_reproduce_exactly(tmp_path, monkeypatch):
    """The deterministic path through the REAL pipeline must be bit-identical run to run."""
    import db.database as _db
    import storage

    # render_once repoints these module globals at a throwaway sandbox; undo it afterwards.
    monkeypatch.setattr(_db, "engine", _db.engine)
    monkeypatch.setattr(storage, "RENDERS_DIR", storage.RENDERS_DIR)
    corpus = rb.CORPUS[:6]
    snaps = []
    for n in range(2):
        root = tmp_path / f"r{n}"
        root.mkdir()
        gen, aligned = rb.render_once("fake", corpus, root)
        snaps.append(rb.snapshot(gen, "fake", corpus, aligned))
    rep = rb.diff_snapshots(snaps[0], snaps[1], exact=True)
    assert rep.ok, rep.failures
    assert snaps[0]["verify"]["ok"]


# ---------------------------------------------------------------- aligned words / tiers
def _aligned(shift=0.0, words=("Hello", "brave", "new", "world")):
    return {"Hello brave new world": [[w, 100 * i + shift, 100 * i + 90 + shift, "aligned"]
                                      for i, w in enumerate(words)]}


def _with_aligned(snap, aligned):
    snap = copy.deepcopy(snap)
    snap["aligned"] = aligned
    return snap


def test_changed_word_text_is_caught():
    g = _with_aligned(_snap(), _aligned())
    f = _with_aligned(_snap(), _aligned(words=("Hello", "brave", "old", "world")))
    assert any("words differ" in m for m in rb.diff_snapshots(g, f).failures)


def test_exact_mode_catches_a_small_systematic_timing_shift():
    g = _with_aligned(_snap(), _aligned())
    f = _with_aligned(_snap(), _aligned(shift=3))  # every word 3 ms late
    assert not rb.diff_snapshots(g, f, exact=True).ok


def test_statistical_mode_catches_words_moving_within_a_sentence():
    g = _with_aligned(_snap(), _aligned())
    moved = _aligned()
    for w in moved["Hello brave new world"]:
        w[1] += 60  # every word starts 60 ms later, ~15% of this sentence
    assert not rb.diff_snapshots(g, _with_aligned(_snap(), moved)).ok


def test_aligned_words_must_spell_the_sentence():
    wrong = {"Hello brave new world": [["Hello", 0, 90, "a"], ["brave", 100, 190, "a"],
                                       ["new", 200, 290, "a"]]}  # a word is missing
    rep = rb.diff_snapshots(_with_aligned(_snap(), wrong), _with_aligned(_snap(), wrong))
    assert any("do not spell" in m for m in rep.failures)


def test_tier_start_must_agree_with_the_base_tier():
    fresh = _snap()
    base = fresh["manifest"]["tiers"]["1.0"]
    fresh["manifest"]["tiers"]["2.0"] = copy.deepcopy(base)
    fresh["manifest"]["tiers"]["2.0"]["requested_factor"] = 2.0
    fresh["manifest"]["tiers"]["2.0"]["actual_factor"] = 2.0
    # not scaled: sentence 1 still starts at 520 ms instead of ~260 ms
    golden = copy.deepcopy(fresh)
    rep = rb.diff_snapshots(golden, fresh)
    assert any("base/factor" in m for m in rep.failures)


# ------------------------------------------------------------------ resume equivalence
@pytest.mark.skipif(not export_encoding.ffmpeg_available(), reason="needs ffmpeg")
def test_resumed_render_equals_an_uninterrupted_one(tmp_path, monkeypatch):
    """Kill a render part-way, resume it from its chunk files, and the published result must
    match a render that was never interrupted (exact: the fake engine is deterministic)."""
    from datetime import datetime, timezone

    import db.database as _db
    import storage
    from services import render_jobs as rj
    from services import render_worker as rw

    monkeypatch.setattr(_db, "engine", _db.engine)
    monkeypatch.setattr(storage, "RENDERS_DIR", storage.RENDERS_DIR)
    corpus = rb.CORPUS[:8]

    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    gen, aligned = rb.render_once("fake", corpus, clean_root)
    clean = rb.snapshot(gen, "fake", corpus, aligned)

    class Boom(Exception):
        pass

    class DiesAt(rb.FakeSynth):
        def __init__(self, n):
            self.n, self.calls = n, 0

        def __call__(self, text, voice):
            self.calls += 1
            if self.calls == self.n:
                raise Boom("simulated crash")
            return super().__call__(text, voice)

    root = tmp_path / "resumed"
    root.mkdir()
    rb.make_sandbox(root, corpus, rb.VOICES["fake"])
    now = datetime.now(timezone.utc).replace(year=2030)
    assert rj.claim_next("w", now, lease_s=3600, debounce_s=0) == rb.BOOK_ID
    with pytest.raises(Exception):
        rw.render_book(rb.BOOK_ID, "w", synthesizer=DiesAt(4), aligner=rb.FakeAligner(),
                       tiers=rb.TIERS, now=lambda: now, lease_s=3600, retries=0,
                       raise_errors=True)
    # Back to pending, as an operator or the retry path would: the chunk files stay on disk.
    from sqlmodel import Session
    from db.models import Book
    with Session(_db.engine) as s:
        b = s.get(Book, rb.BOOK_ID)
        b.job_status, b.lease_owner, b.lease_expires = "pending", None, None
        s.add(b)
        s.commit()
    assert rj.claim_next("w2", now, lease_s=3600, debounce_s=0) == rb.BOOK_ID
    counter = rb.FakeSynth()
    calls = []
    synth = lambda t, v: (calls.append(t), counter(t, v))[1]  # noqa: E731
    outcome = rw.render_book(rb.BOOK_ID, "w2", synthesizer=synth, aligner=rb.FakeAligner(),
                             tiers=rb.TIERS, now=lambda: now, lease_s=3600, raise_errors=True)
    assert outcome == rw.READY
    assert len(calls) < len([c for c in corpus if c not in rb.FILTERED]), \
        "resume re-synthesized everything instead of reusing finished chunks"
    gens = sorted(p for p in (storage.RENDERS_DIR / rb.BOOK_ID).iterdir() if p.name.isdigit())
    resumed = rb.snapshot(gens[-1], "fake", corpus, {})
    resumed["aligned"] = clean["aligned"]  # the spy only saw the post-resume sentences
    rep = rb.diff_snapshots(clean, resumed, exact=True)
    assert rep.ok, rep.failures


def test_kokoro_onnx_engines_are_registered_with_tolerances():
    assert rb.VOICES["kokoro-onnx"] == rb.VOICES["kokoro-onnx-replay"] == "af_heart"
    assert "kokoro-onnx-replay" in rb.EXACT_ENGINES and "kokoro-onnx" not in rb.EXACT_ENGINES
    assert rb._base_engine("kokoro-onnx-replay") == "kokoro-onnx"
    fresh = copy.deepcopy(_snap("kokoro-onnx"))
    for tier in fresh["manifest"]["tiers"].values():
        seg = tier["segments"][0]
        seg["end_ms"] += 16  # +4%: inside the ONNX tolerance, outside torch Kokoro's
        seg["words"][-1]["end_ms"] = seg["end_ms"]
    assert rb.diff_snapshots(_snap("kokoro-onnx"), fresh, engine="kokoro-onnx").ok
    assert not rb.diff_snapshots(_snap("kokoro-onnx"), fresh, engine="kokoro-cpu").ok
