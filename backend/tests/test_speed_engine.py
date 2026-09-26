"""Tests for the speech-rate ("speed") path through TTSEngine.

These pin the contract that the engine must never serve or store audio at a
speed other than the one that was asked for, and that the cache key is stable
for speeds that are numerically equal.

Regression context: `_call_kokoro` historically fell back to
`kokoro(text, voice=voice)` (i.e. 1.0x) whenever the installed Kokoro build did
not accept a `speed=` kwarg, but `stream_job` continued to key the resulting
audio on the *requested* speed. That silently and permanently cached 1.0x audio
under e.g. the 1.5x key, so every later request at 1.5x replayed the wrong rate.
"""
import asyncio
import contextlib
import hashlib
import itertools
import time

import numpy as np
import pytest
from sqlmodel import Session, create_engine

from db.models import AudioCache
from routers.tts import _requested_speed
from services.tts_engine import TTSEngine, SynthJob

SAMPLE_RATE = 24000


@pytest.fixture(scope="session")
def test_engine():
    """In-memory SQLite engine for tests."""
    engine = create_engine("sqlite:///:memory:")
    AudioCache.metadata.create_all(engine)
    return engine


def _audio(samples: int = 2400) -> list:
    """A Kokoro-shaped result tuple: (graphemes, phonemes, audio)."""
    return [(None, None, np.full(samples, 0.5, dtype=np.float32))]


def kokoro_speed_unsupported(text, voice=None, **kwargs):
    """Mimics a Kokoro build whose __call__ has no `speed` kwarg."""
    if "speed" in kwargs:
        raise TypeError("__call__() got an unexpected keyword argument 'speed'")
    return _audio()


def kokoro_speed_supported(text, voice=None, speed=1.0):
    """Mimics a Kokoro build that honours `speed` (amplitude encodes the rate)."""
    return [(None, None, np.full(2400, float(speed), dtype=np.float32))]


class TestCacheKeyStability:
    """Numerically equal speeds must produce one cache key, not several."""

    def test_int_and_float_speed_share_a_key(self, test_engine):
        engine = TTSEngine(kokoro_speed_supported)
        assert engine._cache_key("hello", "af_heart", 1) == engine._cache_key(
            "hello", "af_heart", 1.0
        )

    def test_trailing_float_noise_is_normalised(self, test_engine):
        engine = TTSEngine(kokoro_speed_supported)
        assert engine._cache_key("hello", "af_heart", 1.15) == engine._cache_key(
            "hello", "af_heart", 1.1500000000000001
        )

    def test_distinct_speeds_still_differ(self, test_engine):
        engine = TTSEngine(kokoro_speed_supported)
        assert engine._cache_key("hello", "af_heart", 1.0) != engine._cache_key(
            "hello", "af_heart", 1.5
        )


class TestSpeedFallbackDoesNotPoisonCache:
    """When Kokoro cannot honour the requested speed, nothing may pretend it did."""

    @pytest.mark.asyncio
    async def test_wrong_speed_audio_is_not_cached_under_requested_speed(
        self, test_engine
    ):
        """The 1.5x key must not end up holding 1.0x audio."""
        engine = TTSEngine(kokoro_speed_unsupported)
        job = SynthJob(sentence_index=0, text="Hello world.", voice="af_heart", speed=1.5)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            async for _ in engine.stream_job(job):
                pass

        with Session(test_engine) as session:
            wrongly_cached = session.get(
                AudioCache, engine._cache_key("Hello world.", "af_heart", 1.5)
            )
        assert wrongly_cached is None, (
            "audio synthesised at 1.0x was stored under the 1.5x cache key"
        )

    @pytest.mark.asyncio
    async def test_fallback_reports_the_effective_speed(self, test_engine):
        """Callers must be able to tell that the requested speed was not honoured."""
        engine = TTSEngine(kokoro_speed_unsupported)
        job = SynthJob(sentence_index=0, text="Hello world.", voice="af_heart", speed=1.5)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            async for _ in engine.stream_job(job):
                pass

        meta = engine._sentence_meta.get(0, {})
        assert meta.get("effective_speed") == 1.0, (
            "sentence metadata must expose the speed actually used"
        )
        assert meta.get("requested_speed") == 1.5

    @pytest.mark.asyncio
    async def test_supported_speed_is_cached_under_requested_key(self, test_engine):
        """Regression guard: the happy path must still cache under the asked-for speed."""
        engine = TTSEngine(kokoro_speed_supported)
        job = SynthJob(sentence_index=0, text="Good speed.", voice="af_heart", speed=1.5)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            async for _ in engine.stream_job(job):
                pass

        with Session(test_engine) as session:
            cached = session.get(
                AudioCache, engine._cache_key("Good speed.", "af_heart", 1.5)
            )
        assert cached is not None

    @pytest.mark.asyncio
    async def test_fallback_audio_is_reusable_at_the_speed_it_was_made(
        self, test_engine
    ):
        """1.0x audio produced during a 1.5x request should still be a cache hit at 1.0x."""
        engine = TTSEngine(kokoro_speed_unsupported)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            async for _ in engine.stream_job(
                SynthJob(sentence_index=0, text="Reusable.", voice="af_heart", speed=1.5)
            ):
                pass

        with Session(test_engine) as session:
            reusable = session.get(
                AudioCache, engine._cache_key("Reusable.", "af_heart", 1.0)
            )
        assert reusable is not None, "1.0x fallback audio was cached nowhere at all"


class TestPrefetchRespectsSpeed:
    """Prefetch must obey the same speed contract as streaming."""

    @pytest.mark.asyncio
    async def test_prefetch_does_not_cache_wrong_speed(self, test_engine):
        engine = TTSEngine(kokoro_speed_unsupported)
        sentences = {0: {"text": "Prefetched.", "filtered": False}}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            await engine.prefetch(
                sentences,
                from_index=0,
                count=1,
                voice="af_heart",
                speed=2.0,
                cancel=asyncio.Event(),
            )

        with Session(test_engine) as session:
            wrongly_cached = session.get(
                AudioCache, engine._cache_key("Prefetched.", "af_heart", 2.0)
            )
        assert wrongly_cached is None


class TestCacheKeyBackwardCompatibility:
    """Normalising the speed must not change the key for speeds already shipped.

    `_cache_key` is the only producer of `AudioCache.text_hash`, and the live
    database holds hundreds of megabytes of rows written with the legacy
    `f"{speed}"` spelling. A normalisation that renders 1.0 as "1.00" would
    silently orphan every one of those rows and make the reader re-synthesise
    each sentence once — a slow, user-visible regression that no crash would
    reveal.
    """

    # The speeds the reader actually offers (`MediaBar.svelte:24` is the only
    # place that list exists), plus every other multiple of 0.25 the API can be
    # handed by a client that is not the UI. Every value here must keep its
    # legacy key spelling.
    UI_SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]
    MULTIPLES_OF_025 = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]

    @staticmethod
    def _legacy_key(text: str, voice: str, speed: float) -> str:
        """Reproduces the pre-fix key derivation, verbatim."""
        return hashlib.sha256(f"{text}:{voice}:{speed}".encode()).hexdigest()

    @pytest.mark.parametrize("speed", UI_SPEEDS)
    def test_ui_speeds_still_map_to_their_legacy_key(self, speed):
        engine = TTSEngine(kokoro_speed_supported)
        assert engine._cache_key("Hello world.", "af_heart", speed) == self._legacy_key(
            "Hello world.", "af_heart", speed
        ), f"speed {speed} no longer resolves to its legacy cache key"

    @pytest.mark.parametrize("speed", MULTIPLES_OF_025)
    def test_every_quarter_step_maps_to_its_legacy_key(self, speed):
        """The API accepts any float, so guard the rest of the 0.25 grid too."""
        engine = TTSEngine(kokoro_speed_supported)
        assert engine._cache_key("Hello world.", "af_heart", speed) == self._legacy_key(
            "Hello world.", "af_heart", speed
        ), f"speed {speed} no longer resolves to its legacy cache key"

    def test_int_speed_still_reaches_float_rows(self):
        """A caller passing `speed=1` must still find rows written for `1.0`."""
        engine = TTSEngine(kokoro_speed_supported)
        assert engine._cache_key("Hello.", "af_heart", 1) == self._legacy_key(
            "Hello.", "af_heart", 1.0
        )


class TestTransportSpeedValidation:
    """`speed` is validated and quantised once, at the transport boundary.

    Without this a client could send 0 (which divides by zero inside Kokoro into
    a `RuntimeError: repeats can not be negative` that nothing on the synthesis
    path catches), NaN, or an unquantised value that would render at one rate and
    be filed under another.
    """

    @pytest.mark.parametrize("raw", [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0])
    def test_ui_speeds_pass_through_unchanged(self, raw):
        assert _requested_speed({"speed": raw}) == raw

    def test_missing_speed_defaults_to_one(self):
        assert _requested_speed({}) == 1.0

    def test_speed_is_quantised_to_two_decimals(self):
        """Matching the cache key's normalisation, so key and render agree."""
        assert _requested_speed({"speed": 1.149}) == 1.15

    @pytest.mark.parametrize("raw", [0, 0.0, -1.0, -0.5])
    def test_non_positive_speeds_are_rejected(self, raw):
        with pytest.raises(ValueError):
            _requested_speed({"speed": raw})

    @pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_speeds_are_rejected(self, raw):
        with pytest.raises(ValueError):
            _requested_speed({"speed": raw})

    @pytest.mark.parametrize("raw", ["fast", None, [1.5], {"a": 1}])
    def test_non_numeric_speeds_are_rejected(self, raw):
        with pytest.raises(ValueError):
            _requested_speed({"speed": raw})

    def test_out_of_band_speeds_are_clamped(self):
        assert _requested_speed({"speed": 99}) == 3.0
        assert _requested_speed({"speed": 0.001}) == 0.5

    def test_the_quantised_value_is_what_the_cache_key_uses(self):
        """Key, Kokoro argument and reported rate must not disagree."""
        engine = TTSEngine(kokoro_speed_supported)
        speed = _requested_speed({"speed": 1.149})
        assert speed == 1.15
        assert engine._cache_key("t", "af_heart", speed) == engine._cache_key(
            "t", "af_heart", 1.15
        )


class TestSynthesisDoesNotBlockTheEventLoop:
    """The measured cause of the reader's lag, pinned as a regression test.

    Synthesis used to run synchronously *on* the asyncio event loop: for a
    generator-based Kokoro a single `next()` performs the entire inference, so one
    sentence froze the loop for tens of seconds — measured at 83 consecutive
    seconds with zero event-loop turns. WebSocket frames, `/health` and asyncio
    cancellation were all stuck behind it, which is what made the app feel hung
    and made pause/seek unresponsive.
    """

    @pytest.mark.asyncio
    async def test_the_loop_ticks_while_a_slow_sentence_is_synthesised(self, test_engine):
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.005)

        def slow_kokoro(text, voice=None, speed=1.0):
            time.sleep(0.30)  # stands in for real inference
            return [(None, None, np.full(2400, 0.5, dtype=np.float32))]

        engine = TTSEngine(slow_kokoro)
        beat = asyncio.create_task(heartbeat())
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("services.tts_engine._db.engine", test_engine)
                async for _ in engine.stream_job(
                    SynthJob(sentence_index=0, text="A slow sentence.", speed=1.0)
                ):
                    pass
        finally:
            beat.cancel()

        assert ticks >= 10, (
            f"the event loop ticked only {ticks} times during a 0.30s synthesis, "
            "so synthesis is still running on the loop"
        )

    @pytest.mark.asyncio
    async def test_prefetch_stands_down_while_playback_needs_the_worker(self, test_engine):
        """A batch must not hold the single synthesis worker while audio is awaited."""
        from services import tts_engine as engine_module

        synthesised: list[str] = []

        def kokoro(text, voice=None, speed=1.0):
            synthesised.append(text)
            return [(None, None, np.full(240, 0.5, dtype=np.float32))]

        sentences = {i: {"text": f"Batch sentence {i}.", "filtered": False} for i in range(20)}
        engine = TTSEngine(kokoro)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            # Pretend the user is waiting for audio for the whole window.
            engine_module._playback_wait_begin()
            task = asyncio.create_task(engine.prefetch(
                sentences, from_index=0, count=20, voice="af_heart",
                speed=1.0, cancel=asyncio.Event(),
            ))
            await asyncio.sleep(0.25)
            try:
                assert synthesised == [], (
                    "prefetch claimed the synthesis worker while playback was waiting"
                )
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                engine_module._playback_wait_end()


class TestPrefetchAudioBudget:
    """Prefetch is bounded by listening time, not by how many sentences it touched.

    The old bound was `count=50` sentences, which is silent about how much audio
    the user actually has buffered — 50 sentences is roughly 185 s of audio on
    this corpus and needs about 478 s to generate on CPU, so a burst could never
    finish before cancellation and just held the synthesis worker.
    """

    @staticmethod
    def _kokoro(samples_per_sentence: int = 2400, speed_scaled: bool = False):
        """A Kokoro stub that records its calls. 2400 samples == 0.1 s of audio."""
        calls: list[str] = []

        def kokoro(text, voice=None, speed=1.0):
            calls.append(text)
            count = samples_per_sentence
            if speed_scaled:
                count = max(1, int(samples_per_sentence / speed))
            return [(None, None, np.full(count, 0.5, dtype=np.float32))]

        return kokoro, calls

    _seq = itertools.count()

    @classmethod
    def _sentences(cls, n: int = 20) -> dict:
        """Sentences whose text is unique per call.

        The in-memory engine is session-scoped, so a text reused across two tests
        is a cache hit in the second one and the synthesis count silently comes
        out short. Unique text per call keeps each test's cache isolated.
        """
        tag = next(cls._seq)
        return {i: {"text": f"Budget {tag} sentence {i}.", "filtered": False} for i in range(n)}

    @pytest.mark.asyncio
    async def test_stops_once_the_audio_budget_is_buffered(self, test_engine):
        kokoro, calls = self._kokoro(samples_per_sentence=2400)  # 0.1 s each
        engine = TTSEngine(kokoro)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            await engine.prefetch(
                self._sentences(), from_index=0, count=20, voice="af_heart", speed=1.0,
                cancel=asyncio.Event(), target_audio_seconds=0.25,
            )

        assert len(calls) == 3, (
            f"synthesised {len(calls)} sentences for a 0.25s budget of 0.1s each; "
            "the batch should stop as soon as the budget is met"
        )

    @pytest.mark.asyncio
    async def test_sentence_count_remains_a_hard_cap(self, test_engine):
        """A generous budget must not remove the safety cap."""
        kokoro, calls = self._kokoro(samples_per_sentence=240)  # 0.01 s each
        engine = TTSEngine(kokoro)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            await engine.prefetch(
                self._sentences(), from_index=0, count=5, voice="af_heart", speed=1.0,
                cancel=asyncio.Event(), target_audio_seconds=600.0,
            )

        assert len(calls) == 5

    @pytest.mark.asyncio
    async def test_a_faster_speed_covers_more_sentences_for_the_same_budget(self, test_engine):
        """A time budget adapts to speed; a sentence count cannot."""
        counts: dict[float, int] = {}
        sentences = self._sentences()
        for speed in (1.0, 2.0):
            kokoro, calls = self._kokoro(samples_per_sentence=2400, speed_scaled=True)
            engine = TTSEngine(kokoro)
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("services.tts_engine._db.engine", test_engine)
                await engine.prefetch(
                    sentences, from_index=0, count=20, voice="af_heart",
                    speed=speed, cancel=asyncio.Event(), target_audio_seconds=0.25,
                )
            counts[speed] = len(calls)

        assert counts[2.0] > counts[1.0], (
            f"the same audio budget should reach further at 2x, got {counts}"
        )

    @pytest.mark.asyncio
    async def test_cached_audio_counts_towards_the_budget_without_resynthesising(
        self, test_engine
    ):
        sentences = self._sentences(4)
        seed_kokoro, _ = self._kokoro(samples_per_sentence=2400)  # 0.1 s each
        seeder = TTSEngine(seed_kokoro)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            # Warm all four (0.4 s of audio) with a budget large enough to do it.
            await seeder.prefetch(
                sentences, from_index=0, count=4, voice="af_heart", speed=1.0,
                cancel=asyncio.Event(), target_audio_seconds=600.0,
            )

            kokoro, calls = self._kokoro(samples_per_sentence=2400)
            engine = TTSEngine(kokoro)
            await engine.prefetch(
                sentences, from_index=0, count=4, voice="af_heart", speed=1.0,
                cancel=asyncio.Event(), target_audio_seconds=0.25,
            )

        assert calls == [], (
            "already-buffered audio must satisfy the budget rather than be regenerated"
        )

    @pytest.mark.asyncio
    async def test_a_zero_budget_disables_prefetch(self, test_engine):
        kokoro, calls = self._kokoro()
        engine = TTSEngine(kokoro)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.tts_engine._db.engine", test_engine)
            await engine.prefetch(
                self._sentences(), from_index=0, count=20, voice="af_heart", speed=1.0,
                cancel=asyncio.Event(), target_audio_seconds=0,
            )

        assert calls == []
