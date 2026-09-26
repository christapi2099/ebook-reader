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

import numpy as np
import pytest
from sqlmodel import Session, create_engine

from db.models import AudioCache
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
