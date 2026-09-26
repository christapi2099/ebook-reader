"""Adversarial tests for the speech-rate ("speed") path in services/tts_engine.py.

Purpose: FALSIFY commit `cb2d853` ("stop caching audio under a speed it was not
synthesised at"). Every test here is an attempted attack with a recorded outcome.
Three of them PASS today and therefore document real residual holes; the rest
fail-closed as invariants that the fix does satisfy.

Attack log (see also the docstring on each test):

  A1  Any remaining cache-write path keyed on the requested speed instead of the
      effective one ......... NOT BROKEN. `TestEveryRowIsKeyedOnTheRateActuallyRendered`
                              drives both writers and checks every row's key.
  A2  Over-broad `except TypeError` in `_call_kokoro` ......... FIXED. `_call_kokoro`
                              now decides capability by inspecting the signature
                              rather than probing with a call, and `prefetch` guards
                              `kokoro is None` the way `stream_job` already did. The
                              two tests below are retained as regression tests for
                              the fix rather than as bug documentation.
  A3  A row already on disk at the wrong rate (written by pre-fix code)
      ............................ BROKEN (by construction): the fix neither
                              detects nor invalidates it, and now labels it as
                              honoured. `TestLegacyPoisonedRow`.
  A4  Mixed rates inside one session when the engine downgrades mid-stream
      ............................ BROKEN: cache hits at the requested speed and
                              fresh renders at 1.0x coexist. `TestMixedRateInOneSession`.
  A5  `repr(round(speed, 2))` colliding two distinct speeds ......... BROKEN for
                              client-supplied speeds, bounded to <1% rate error and
                              unreachable from the UI. `TestCacheKeyCollisions`.

Note for A2/A4: with the *installed* Kokoro (0.9.4) neither can produce wrong-key
audio. `KPipeline.__call__` is a generator function, so only argument binding runs
at call time; the only TypeError reachable there is a genuine signature mismatch
(verified: `inspect.isgeneratorfunction` is True and a bogus kwarg raises before
any body statement executes). A2 and A4 are therefore contract fragilities that
bend only for non-generator bindings, and A3/A4 are the reason the user still
needs to be told the requested rate was not honoured.
"""
import asyncio
import hashlib
import io
from datetime import UTC, datetime

import numpy as np
import pytest
import soundfile as sf
from sqlmodel import Session, create_engine, select

from db.models import AudioCache
from services.tts_engine import TTSEngine, SynthJob

SAMPLE_RATE = 24000
# The speeds the reader actually offers. `MediaBar.svelte` holds the only copy of
# that list; 3.0 was removed in 36e2120 because Kokoro cannot deliver it. The API
# accepts any float, so client-supplied values the UI cannot produce are covered
# separately by TestCacheKeyCollisions.
UI_SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

# Amplitude is used as a witness for "which code path rendered this audio".
AMP_FALLBACK_1X = 0.20  # kokoro(text, voice=voice)      -> 1.0x
AMP_HONOURED_1_5 = 0.60  # kokoro(text, voice=voice, speed=1.5)
AMP_PRE_FIX_POISON = 0.10  # 1.0x audio a pre-fix build wrote under a 1.5x key


@pytest.fixture(scope="session")
def test_engine():
    """In-memory SQLite engine for tests.

    Session-scoped and therefore SHARED across this file (matching the existing
    suite), so every test below uses distinct sentence text to stay isolated.
    """
    engine = create_engine("sqlite:///:memory:")
    AudioCache.metadata.create_all(engine)
    return engine


def _results(amplitude: float) -> list:
    return [(None, None, np.full(2400, amplitude, dtype=np.float32))]


def _decode(chunks: list[bytes]) -> np.ndarray:
    """Concatenate the WAV chunks the engine yields into one float array."""
    parts = [sf.read(io.BytesIO(c))[0] for c in chunks]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


async def _drain(engine: TTSEngine, job: SynthJob) -> list[bytes]:
    return [chunk async for chunk in engine.stream_job(job)]


def _amp(db_engine, tts_engine: TTSEngine, text: str, voice: str, speed: float) -> float | None:
    """Mean amplitude of the cached row at (text, voice, speed), or None."""
    with Session(db_engine) as session:
        row = session.get(AudioCache, tts_engine._cache_key(text, voice, speed))
    if row is None:
        return None
    return float(np.frombuffer(row.audio_data, dtype=np.int16).astype(np.float32).mean() / 32767.0)


class UnsupportedKokoro:
    """A binding whose __call__ has no `speed` kwarg (the fallback trigger)."""

    def __init__(self, amplitude: float = AMP_FALLBACK_1X):
        self.amplitude = amplitude
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, text, voice=None, **kwargs):
        self.calls.append((text, dict(kwargs)))
        if "speed" in kwargs:
            raise TypeError("__call__() got an unexpected keyword argument 'speed'")
        return _results(self.amplitude)


class HonouringKokoro:
    """A binding that accepts and honours `speed`; records what it was asked for."""

    def __init__(self, amplitude: float = AMP_HONOURED_1_5):
        self.amplitude = amplitude
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, text, voice=None, speed=1.0):
        self.calls.append((text, {"speed": speed}))
        return _results(self.amplitude)


# ---------------------------------------------------------------------------
# A1 — is ANY cache row still keyed on the requested speed?
# ---------------------------------------------------------------------------


class TestEveryRowIsKeyedOnTheRateActuallyRendered:
    """Direct falsification attempt on attack A1.

    Drive both cache writers (stream_job and prefetch) through a binding that
    rejects `speed=`, and assert that (a) no row exists under the *requested*
    key, and (b) every row that does exist is exactly the key derived from the
    rate the binding was actually called with.
    """

    @pytest.mark.asyncio
    async def test_stream_job_writes_only_effective_speed_keys(self, test_engine, monkeypatch):
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        kokoro = UnsupportedKokoro()
        engine = TTSEngine(kokoro)
        text = "A1 stream: the requested rate is not the rendered rate."

        chunks = await _drain(engine, SynthJob(sentence_index=0, text=text, voice="af_heart", speed=1.5))
        assert chunks, "engine produced no audio"

        # Capability is decided by inspecting the signature, so the binding is
        # never speculatively called with a kwarg it cannot accept.
        assert kokoro.calls == [(text, {})], (
            "an unsupported binding must be called exactly once, without speed="
        )

        assert _amp(test_engine, engine, text, "af_heart", 1.5) is None, (
            "A1 BROKEN: a row exists under the requested 1.5x key after a 1.0x render"
        )
        assert _amp(test_engine, engine, text, "af_heart", 1.0) == pytest.approx(AMP_FALLBACK_1X, abs=0.01)

    @pytest.mark.asyncio
    async def test_prefetch_writes_only_effective_speed_keys(self, test_engine, monkeypatch):
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        kokoro = UnsupportedKokoro()
        engine = TTSEngine(kokoro)
        text = "A1 prefetch: the requested rate is not the rendered rate."
        sentences = {0: {"text": text, "filtered": False}}

        await engine.prefetch(
            sentences, from_index=0, count=1, voice="af_heart", speed=2.0,
            cancel=asyncio.Event(),
        )

        assert _amp(test_engine, engine, text, "af_heart", 2.0) is None, (
            "A1 BROKEN: prefetch stored a 1.0x render under the requested 2.0x key"
        )
        assert _amp(test_engine, engine, text, "af_heart", 1.0) == pytest.approx(AMP_FALLBACK_1X, abs=0.01)

    @pytest.mark.asyncio
    async def test_no_row_under_any_requested_key_for_a_whole_run(self, test_engine, monkeypatch):
        """Property check: set(rows present) == set(keys for rendered rates)."""
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        kokoro = UnsupportedKokoro()
        engine = TTSEngine(kokoro)
        texts = [f"A1 property sentence number {i}." for i in range(3)]

        for i, text in enumerate(texts):
            await _drain(engine, SynthJob(sentence_index=i, text=text, voice="af_heart", speed=1.25))

        expected = {engine._cache_key(t, "af_heart", 1.0) for t in texts}
        with Session(test_engine) as session:
            present = {row.text_hash for row in session.exec(select(AudioCache)).all()}
        missing = expected - present
        assert not missing, f"A1: expected 1.0x rows are missing: {missing}"
        for t in texts:
            for requested in (0.5, 1.25, 1.5, 2.0, 3.0):
                assert engine._cache_key(t, "af_heart", requested) not in present, (
                    f"A1 BROKEN: row present under requested key {requested} for {t!r}"
                )


# ---------------------------------------------------------------------------
# A2 — the `except TypeError` in _call_kokoro is over-broad
# ---------------------------------------------------------------------------


class TestOverBroadTypeErrorCatch:
    """Attempt to make an UNRELATED TypeError downgrade a healthy engine."""

    @pytest.mark.asyncio
    async def test_prefetch_with_kokoro_none_leaves_flag_undecided(
        self, test_engine, monkeypatch
    ):
        """A2 FIXED: `prefetch` now guards `kokoro is None` exactly as `stream_job` does.

        Previously the missing guard let `_call_kokoro` catch
        `TypeError: 'NoneType' object is not callable`, misread it as "this build
        has no speed support", log that false line, and pin the sticky flag —
        corrupting the capability probe because Kokoro failed to load, which has
        nothing to do with `speed`.
        """
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        engine = TTSEngine(None)
        assert engine._speed_kwarg_supported is None

        await engine.prefetch(
            {0: {"text": "A2 none-callable sentence.", "filtered": False}},
            from_index=0, count=1, voice="af_heart", speed=1.5,
            cancel=asyncio.Event(),
        )

        assert engine._speed_kwarg_supported is None, (
            "a missing Kokoro must never be mistaken for a speed-capability problem"
        )

    @pytest.mark.asyncio
    async def test_stream_job_with_kokoro_none_leaves_flag_undecided(self, test_engine, monkeypatch):
        """Control for the test above: stream_job's guard keeps the probe clean."""
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        engine = TTSEngine(None)
        chunks = await _drain(engine, SynthJob(sentence_index=0, text="A2 control.", speed=1.5))
        assert chunks == []
        assert engine._speed_kwarg_supported is None, (
            "stream_job must not touch the capability probe when kokoro is absent"
        )

    @pytest.mark.asyncio
    async def test_unrelated_typeerror_does_not_downgrade_a_healthy_engine(
        self, test_engine, monkeypatch
    ):
        """A2 FIXED for non-generator bindings.

        This binding raises TypeError for one *text*, not for its signature. The
        real Kokoro cannot behave this way (its __call__ is a generator, so only
        argument binding runs at call time), but any wrapper that returns a list
        can — and the new Modal remote client added in e301ddf is exactly such a
        wrapper. Capability is now decided by signature, so the fault propagates
        as a genuine error instead of being misread as "no speed support", and the
        NEXT sentence keeps its requested rate.
        """
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        calls: list[dict] = []

        def text_specific_typeerror(text, voice=None, speed=1.0):
            calls.append({"text": text, "speed": speed})
            if "BOOM" in text:
                raise TypeError("unrelated: this text hit a non-generator code path")
            return _results(AMP_HONOURED_1_5)

        engine = TTSEngine(text_specific_typeerror)
        healthy = "A2 healthy sentence after the failure."

        # 1. The fault surfaces instead of being swallowed as a capability probe.
        with pytest.raises(TypeError):
            await _drain(engine, SynthJob(sentence_index=0, text="BOOM sentence.", speed=1.5))
        assert engine._speed_kwarg_supported is True, (
            "a fault unrelated to the signature must not downgrade the engine"
        )

        # 2. The next healthy sentence still gets the rate the user asked for.
        await _drain(engine, SynthJob(sentence_index=1, text=healthy, voice="af_heart", speed=1.5))

        assert calls[-1]["speed"] == 1.5, "the healthy sentence lost its requested rate"
        assert _amp(test_engine, engine, healthy, "af_heart", 1.5) == pytest.approx(AMP_HONOURED_1_5, abs=0.01)
        assert _amp(test_engine, engine, healthy, "af_heart", 1.0) is None, (
            "the healthy sentence was silently rendered at 1.0x"
        )


# ---------------------------------------------------------------------------
# A3 — a row already on disk at the wrong rate
# ---------------------------------------------------------------------------


class TestLegacyPoisonedRow:
    """A3 BROKEN: the fix cannot detect a row that pre-fix code poisoned."""

    @pytest.mark.asyncio
    async def test_prefix_row_is_still_served_and_now_mislabelled_as_honoured(
        self, test_engine, monkeypatch
    ):
        """Simulate a row written by pre-cb2d853 code, then request that speed.

        The fix's cache lookup is keyed on the *optimistically assumed* effective
        speed while `_speed_kwarg_supported` is still None, so it finds and serves
        the poisoned row, and the new `effective_speed` field reports the request
        as honoured. A UI warning built on `effective_speed` would therefore stay
        silent for exactly the rows the fix was written to prevent.
        """
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        engine = TTSEngine(UnsupportedKokoro())
        text = "A3 row poisoned before the fix was applied."

        # Pre-seed the cache exactly as the buggy code would have left it.
        pcm = (np.full(2400, AMP_PRE_FIX_POISON, dtype=np.float32) * 32767).astype(np.int16).tobytes()
        with Session(test_engine) as session:
            session.add(AudioCache(
                text_hash=engine._cache_key(text, "af_heart", 1.5),
                audio_data=pcm,
                duration_ms=100,
                voice="af_heart",
                word_timestamps=None,
                created_at=datetime.now(UTC),
            ))
            session.commit()

        chunks = await _drain(engine, SynthJob(sentence_index=0, text=text, voice="af_heart", speed=1.5))
        served = _decode(chunks)

        assert served.size > 0 and np.allclose(served.mean(), AMP_PRE_FIX_POISON, atol=0.01), (
            "A3: expected the pre-fix row to be served verbatim to a 1.5x request"
        )
        meta = engine._sentence_meta[0]
        assert meta["effective_speed"] == 1.5, (
            "A3 BROKEN: the poisoned row is reported as an honoured 1.5x render, so a "
            "downgrade warning keyed on effective_speed will not fire"
        )
        assert engine._speed_kwarg_supported is None, (
            "no synthesis happened, so the engine never discovered the downgrade"
        )

    @pytest.mark.asyncio
    async def test_fix_does_not_create_new_poison_on_a_miss(self, test_engine, monkeypatch):
        """Control: the same binding on a cache MISS does the right thing."""
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        engine = TTSEngine(UnsupportedKokoro())
        text = "A3 control: a miss is handled honestly."
        await _drain(engine, SynthJob(sentence_index=0, text=text, voice="af_heart", speed=1.5))
        assert _amp(test_engine, engine, text, "af_heart", 1.5) is None
        assert engine._sentence_meta[0]["effective_speed"] == 1.0


# ---------------------------------------------------------------------------
# A4 — mixed rates inside a single session
# ---------------------------------------------------------------------------


class TestMixedRateInOneSession:
    """A4 BROKEN: one playback session can deliver two different rates."""

    @pytest.mark.asyncio
    async def test_cached_sentence_plays_at_requested_rate_then_fresh_one_downgrades(
        self, test_engine, monkeypatch
    ):
        """Sentence 1 hits a legitimate 1.5x row; sentence 2 discovers the downgrade.

        Nothing is mis-filed, but the listener hears 1.5x and then 1.0x in the same
        session, and only `_sentence_meta` knows.
        """
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        engine = TTSEngine(UnsupportedKokoro())
        cached_text = "A4 sentence already cached at 1.5x."
        fresh_text = "A4 sentence that has to be synthesised now."

        pcm = (np.full(2400, AMP_HONOURED_1_5, dtype=np.float32) * 32767).astype(np.int16).tobytes()
        with Session(test_engine) as session:
            session.add(AudioCache(
                text_hash=engine._cache_key(cached_text, "af_heart", 1.5),
                audio_data=pcm,
                duration_ms=100,
                voice="af_heart",
                word_timestamps=None,
                created_at=datetime.now(UTC),
            ))
            session.commit()

        first = _decode(await _drain(
            engine, SynthJob(sentence_index=0, text=cached_text, voice="af_heart", speed=1.5)))
        second = _decode(await _drain(
            engine, SynthJob(sentence_index=1, text=fresh_text, voice="af_heart", speed=1.5)))

        assert first.mean() == pytest.approx(AMP_HONOURED_1_5, abs=0.01), "sentence 1 should be 1.5x"
        assert second.mean() == pytest.approx(AMP_FALLBACK_1X, abs=0.01), "sentence 2 should be 1.0x"
        assert engine._sentence_meta[0]["effective_speed"] == 1.5
        assert engine._sentence_meta[1]["effective_speed"] == 1.0, (
            "A4: within one session the effective rate changed from 1.5x to 1.0x; "
            "the requested rate is now unhonoured mid-sentence-run"
        )


# ---------------------------------------------------------------------------
# A5 — does `repr(round(speed, 2))` collide two distinct speeds?
# ---------------------------------------------------------------------------


class TestChunkingDoesNotChangeTheRate:
    """Part 3 control: locate the quantisation.

    `stream_job` slices the Kokoro output into `SAMPLE_RATE // 10` = 2400-sample
    (100 ms) WAV chunks. Slicing at a fixed sample rate cannot change the rate, so
    if the total sample count equals what Kokoro produced, and every chunk claims
    24000 Hz, the chunking layer is exonerated and all quantisation lives in
    Kokoro (`torch.round(duration / speed).clamp(min=1)`, a 25 ms frame grid).
    `_proportional_timestamps` is likewise exonerated: it only re-divides an
    already-final `duration_ms` across words and is never called on the
    fresh-synthesis path.
    """

    @pytest.mark.asyncio
    async def test_total_samples_and_rate_are_preserved(self, test_engine, monkeypatch):
        monkeypatch.setattr("services.tts_engine._db.engine", test_engine)
        engine = TTSEngine(HonouringKokoro())
        text = "Part 3 control: chunking must not resample."

        chunks = await _drain(engine, SynthJob(sentence_index=0, text=text, voice="af_heart", speed=1.5))
        assert chunks, "expected audio chunks"

        total = 0
        for c in chunks:
            data, rate = sf.read(io.BytesIO(c))
            assert rate == SAMPLE_RATE, "chunk was not written at the engine sample rate"
            total += data.size

        assert total == 2400, (
            f"chunking changed the sample count (got {total}, Kokoro produced 2400); "
            "that would mean the rate is being altered outside Kokoro"
        )
        # duration reported for the row must agree with the sample count exactly
        assert engine._sentence_meta[0]["duration_ms"] == 100


class TestCacheKeyCollisions:
    """A5: collisions exist for client-supplied speeds; quantify the damage."""

    def test_ui_speeds_never_collide(self):
        engine = TTSEngine(None)
        keys = [engine._cache_key("hello", "af_heart", s) for s in UI_SPEEDS]
        assert len(set(keys)) == len(UI_SPEEDS), "two UI speeds share a cache key"

    def test_rounding_is_lossless_for_every_ui_speed(self):
        """The normalisation must not change the key for anything the UI can send."""
        engine = TTSEngine(None)
        for speed in UI_SPEEDS:
            legacy = hashlib.sha256(f"hello:af_heart:{speed}".encode()).hexdigest()
            assert engine._cache_key("hello", "af_heart", speed) == legacy, speed

    def test_distinct_client_speeds_can_collide(self):
        """A5 BROKEN, bounded: two different requested rates can share a key.

        Unreachable from the UI (min UI gap is 0.25) but reachable for any API
        client. Whoever renders first wins, so the second request is served audio
        rendered at the first request's rate.
        """
        engine = TTSEngine(None)
        assert engine._cache_key("hello", "af_heart", 1.149) == engine._cache_key(
            "hello", "af_heart", 1.15
        ), "expected 1.149 and 1.15 to normalise onto the same key"

    def test_worst_case_rate_error_is_bounded_by_the_rounding_step(self):
        """Bound the damage from A5.

        `round(s, 2)` maps every speed onto a 2dp value, so members of one bucket
        span < 0.01. Relative to the slowest reachable speed (0.5x) that is < 2%;
        at 3.0x it is < 0.34%. Measured worst case over the sweep is asserted
        against the derived bound rather than a guessed one.
        """
        engine = TTSEngine(None)
        buckets: dict[str, list[float]] = {}
        step = 1e-4
        n = int(round((3.0 - 0.5) / step)) + 1
        for i in range(n):
            s = round(0.5 + i * step, 6)
            buckets.setdefault(engine._cache_key("hello", "af_heart", s), []).append(s)

        worst_rel = 0.0
        worst_abs = 0.0
        colliding_keys = 0
        for speeds in buckets.values():
            if len(speeds) < 2:
                continue
            colliding_keys += 1
            spread = max(speeds) - min(speeds)
            worst_abs = max(worst_abs, spread)
            worst_rel = max(worst_rel, spread / min(speeds))

        assert colliding_keys > 0, "expected rounding to collide some distinct speeds"
        assert worst_abs < 0.01, f"bucket spread {worst_abs} should stay under the 2dp step"
        assert worst_rel < 0.02, (
            f"worst-case served-rate error {worst_rel:.4%} exceeds the derived 2% bound"
        )
