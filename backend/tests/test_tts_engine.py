"""TDD tests for services/tts_engine.py. Mocks Kokoro so no GPU required.

The cache tests run against a real SQLite database: the autouse guard in
conftest.py points ``db.database.engine`` at a throwaway in-memory engine, so
``stream_job``'s AudioCache reads and writes are exercised for real instead of
being asserted against a Mock of ``Session``.
"""
import json

import pytest
import asyncio
import numpy as np
from unittest.mock import MagicMock, patch, AsyncMock
from sqlmodel import Session

from db.models import AudioCache
from services.tts_engine import SAMPLE_RATE, TTSEngine, SynthJob


def _kokoro_spy(inner=None, samples: int = 2400):
    """A fake pipeline that records every call it receives.

    Returns ``(kokoro, calls)`` where ``calls`` accumulates ``(text, voice,
    speed)`` tuples, so a test can assert what the engine really passed down
    without mocking the engine's own collaborators.
    """
    if inner is None:
        def inner(text, voice="af_heart", speed=1.0):
            yield (None, None, np.ones(samples, dtype=np.float32))

    calls: list[tuple[str, str, float]] = []

    def kokoro(text, voice="af_heart", speed=1.0):
        calls.append((text, voice, speed))
        return inner(text, voice=voice, speed=speed)

    return kokoro, calls


class TestSynthJob:
    def test_has_required_fields(self):
        job = SynthJob(sentence_index=5, text="Hello world.")
        assert job.sentence_index == 5
        assert job.text == "Hello world."
        assert job.voice == "af_heart"  # default voice

    def test_custom_voice(self):
        job = SynthJob(sentence_index=0, text="Hi.", voice="am_adam")
        assert job.voice == "am_adam"


class TestSynthJobSpeed:
    def test_default_speed_is_1(self):
        job = SynthJob(sentence_index=0, text="Test")
        assert job.speed == 1.0

    def test_custom_speed(self):
        job = SynthJob(sentence_index=0, text="Test", speed=1.5)
        assert job.speed == 1.5


class TestTTSEngineInit:
    def test_initializes_with_defaults(self):
        engine = TTSEngine(kokoro=None)
        assert engine.current_voice == "af_heart"
        assert engine.queue.maxsize == 30

    def test_cancelled_set_starts_empty(self):
        engine = TTSEngine(kokoro=None)
        assert len(engine.cancelled) == 0


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_adds_job(self):
        engine = TTSEngine(kokoro=None)
        await engine.enqueue(SynthJob(sentence_index=0, text="Test sentence."))
        assert engine.queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_enqueue_multiple(self):
        engine = TTSEngine(kokoro=None)
        for i in range(3):
            await engine.enqueue(SynthJob(sentence_index=i, text=f"Sentence {i}."))
        assert engine.queue.qsize() == 3


class TestCancellation:
    """Cancellation as it works after ``cancel_from()`` was removed (04d6cfe).

    The responsibility is split in two now:

    * ``TTSEngine.stream_job`` consults ``engine.cancelled`` and stops emitting
      for a cancelled index -- tested here against the real method.
    * ``routers.tts._cancel_and_clear`` owns draining ``engine.queue`` and
      resetting ``engine.cancelled``; that half is covered end to end in
      tests/test_websocket_integration.py (stale-session and pause tests), which
      drive the real WebSocket handler.
    """

    @pytest.mark.asyncio
    async def test_cancelled_job_yields_nothing_and_skips_synthesis(self):
        kokoro, calls = _kokoro_spy()
        engine = TTSEngine(kokoro)
        engine.cancelled.add(0)

        chunks = [
            chunk
            async for chunk in engine.stream_job(SynthJob(sentence_index=0, text="Skipped."))
        ]

        assert chunks == []
        assert calls == [], "a cancelled job must not reach the pipeline"
        assert engine._sentence_meta == {}

    @pytest.mark.asyncio
    async def test_cancelling_mid_stream_stops_the_audio(self, fake_kokoro_factory):
        """Cancelling mid-sentence truncates the stream and skips the cache write."""
        import db.database as database

        engine = TTSEngine(fake_kokoro_factory(4800))  # two 100 ms chunks
        job = SynthJob(sentence_index=0, text="Long sentence.")
        stream = engine.stream_job(job)

        first = await stream.__anext__()
        assert first.startswith(b"RIFF")

        engine.cancelled.add(0)
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()

        with Session(database.engine) as session:
            key = engine._cache_key(job.text, job.voice, job.speed)
            assert session.get(AudioCache, key) is None, "half-synthesized audio must not be cached"
        assert engine._sentence_meta == {}


class TestCacheKey:
    def test_same_text_voice_same_key(self):
        engine = TTSEngine(kokoro=None)
        k1 = engine._cache_key("Hello world.", "af_heart", 1.0)
        k2 = engine._cache_key("Hello world.", "af_heart", 1.0)
        assert k1 == k2

    def test_different_text_different_key(self):
        engine = TTSEngine(kokoro=None)
        k1 = engine._cache_key("Hello.", "af_heart", 1.0)
        k2 = engine._cache_key("Goodbye.", "af_heart", 1.0)
        assert k1 != k2

    def test_different_voice_different_key(self):
        engine = TTSEngine(kokoro=None)
        k1 = engine._cache_key("Hello.", "af_heart", 1.0)
        k2 = engine._cache_key("Hello.", "am_adam", 1.0)
        assert k1 != k2

    def test_key_is_string(self):
        engine = TTSEngine(kokoro=None)
        assert isinstance(engine._cache_key("Test.", "af_heart", 1.0), str)


class TestCacheKeyWithSpeed:
    def test_same_speed_same_key(self):
        engine = TTSEngine(kokoro=None)
        k1 = engine._cache_key("Hello.", "af_heart", 1.0)
        k2 = engine._cache_key("Hello.", "af_heart", 1.0)
        assert k1 == k2

    def test_different_speed_different_key(self):
        engine = TTSEngine(kokoro=None)
        k1 = engine._cache_key("Hello.", "af_heart", 1.0)
        k2 = engine._cache_key("Hello.", "af_heart", 1.5)
        assert k1 != k2


class TestStreamJob:
    """``stream_next()`` was replaced by ``stream_job(job)`` (04d6cfe).

    A job is now handed to the generator directly instead of being pulled off
    ``engine.queue``; ``enqueue``/``queue`` still exist for the router's producer
    task and are covered by TestEnqueue above.
    """

    @pytest.mark.asyncio
    async def test_yields_decodable_wav_chunks_for_valid_job(self):
        import io
        import soundfile as sf

        kokoro, calls = _kokoro_spy(samples=2400)
        engine = TTSEngine(kokoro)
        job = SynthJob(sentence_index=0, text="Hello world this is a test.")

        chunks = [chunk async for chunk in engine.stream_job(job)]

        assert calls == [(job.text, job.voice, job.speed)]
        assert len(chunks) == 1, "2400 samples is exactly one 100 ms chunk"
        assert all(isinstance(c, bytes) for c in chunks)
        decoded, rate = sf.read(io.BytesIO(chunks[0]))
        assert rate == SAMPLE_RATE
        assert len(decoded) == 2400

    @pytest.mark.asyncio
    async def test_yields_nothing_when_no_pipeline_is_loaded(self):
        """The app can start without Kokoro; that must not raise."""
        engine = TTSEngine(kokoro=None)
        chunks = [
            chunk
            async for chunk in engine.stream_job(SynthJob(sentence_index=0, text="Test."))
        ]
        assert chunks == []


class TestAudioCacheIntegration:
    """``stream_job`` cache behaviour against a real AudioCache table.

    The autouse guard in conftest.py points ``db.database.engine`` at a
    throwaway in-memory database, so these tests exercise the real SQLModel
    queries. The old versions asserted against a Mock of ``Session``, which is
    why they could not have caught a broken cache in the first place.
    """

    @pytest.mark.asyncio
    async def test_cache_hit_skips_kokoro_and_replays_identical_audio(self):
        kokoro, calls = _kokoro_spy(samples=2400)
        engine = TTSEngine(kokoro)
        job = SynthJob(sentence_index=0, text="Cached.")

        first = [chunk async for chunk in engine.stream_job(job)]
        assert calls == [("Cached.", "af_heart", 1.0)]
        assert len(first) == 1

        calls.clear()
        engine._sentence_meta.clear()
        second = [chunk async for chunk in engine.stream_job(job)]

        assert calls == [], "cache hit must not call Kokoro again"
        assert second == first, "cache hit must replay the same bytes"
        assert engine._sentence_meta[0]["duration_ms"] == 100

    @pytest.mark.asyncio
    async def test_cache_miss_calls_kokoro_with_the_job_speed(self):
        kokoro, calls = _kokoro_spy(samples=2400)
        engine = TTSEngine(kokoro)
        job = SynthJob(sentence_index=0, text="Uncached.", voice="am_adam", speed=1.5)

        chunks = [chunk async for chunk in engine.stream_job(job)]

        assert chunks
        assert calls == [("Uncached.", "am_adam", 1.5)]

    @pytest.mark.asyncio
    async def test_cache_row_records_key_duration_and_audio(self):
        import db.database as database

        engine = TTSEngine(_kokoro_spy(samples=2400)[0])
        job = SynthJob(sentence_index=0, text="To cache.", voice="af_heart", speed=1.5)

        async for _ in engine.stream_job(job):
            pass

        with Session(database.engine) as session:
            key = engine._cache_key(job.text, job.voice, job.speed)
            entry = session.get(AudioCache, key)
            assert entry is not None
            assert entry.voice == "af_heart"
            assert entry.duration_ms == 100
            assert len(entry.audio_data) == 2400 * 2  # int16 PCM
            assert entry.word_timestamps is None  # the fake result carries no tokens
            # The speed is part of the key, so another speed is a different row.
            assert session.get(AudioCache, engine._cache_key(job.text, job.voice, 1.0)) is None

    @pytest.mark.asyncio
    async def test_cache_write_failure_does_not_break_synthesis(self, monkeypatch):
        """A broken cache must cost the user nothing: audio still streams and the
        duration is still reported, it just cannot be replayed later."""
        engine = TTSEngine(_kokoro_spy(samples=2400)[0])

        def boom(*args, **kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(engine, "_write_cache_entry", boom)
        job = SynthJob(sentence_index=0, text="Cache write fails.")

        chunks = [chunk async for chunk in engine.stream_job(job)]

        assert len(chunks) == 1
        assert chunks[0].startswith(b"RIFF")
        assert engine._sentence_meta[0]["duration_ms"] == 100

    @pytest.mark.asyncio
    async def test_cache_hit_chunks_decode_to_the_original_audio(self, fake_kokoro_factory):
        import io
        import soundfile as sf

        engine = TTSEngine(fake_kokoro_factory(4800))  # two chunks
        job = SynthJob(sentence_index=0, text="Two chunks.")

        chunks = [chunk async for chunk in engine.stream_job(job)]
        assert len(chunks) == 2

        samples = []
        for chunk in chunks:
            data, rate = sf.read(io.BytesIO(chunk))
            assert rate == SAMPLE_RATE
            samples.extend(data)
        assert len(samples) == 4800
        assert all(abs(sample - 1.0) < 0.001 for sample in samples)


class TestKokoroSpeedParam:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("speed", [1.0, 1.5, 2.0, 3.0])
    async def test_speed_reaches_kokoro_and_drives_the_reported_duration(
        self, speed, fake_kokoro_factory
    ):
        """speed goes to KPipeline unchanged, and the duration the router reports
        is derived from the audio Kokoro actually returned (2400/speed samples),
        not recomputed from the requested speed."""
        kokoro, calls = _kokoro_spy(fake_kokoro_factory(2400))
        engine = TTSEngine(kokoro)
        job = SynthJob(sentence_index=0, text="Test", speed=speed)

        chunks = [chunk async for chunk in engine.stream_job(job)]

        assert calls == [("Test", "af_heart", speed)]
        assert chunks
        expected_ms = int(round(2400 / speed) / SAMPLE_RATE * 1000)
        assert engine._sentence_meta[0]["duration_ms"] == expected_ms

    @pytest.mark.asyncio
    async def test_speed_1x_default(self):
        kokoro, calls = _kokoro_spy(samples=2400)
        engine = TTSEngine(kokoro)
        job = SynthJob(sentence_index=0, text="Test")

        chunks = [chunk async for chunk in engine.stream_job(job)]

        assert calls == [("Test", "af_heart", 1.0)]
        assert chunks
        assert engine._sentence_meta[0]["duration_ms"] == 100

    @pytest.mark.asyncio
    async def test_callable_without_speed_kwarg_degrades_to_1x(self):
        """A KPipeline build with no ``speed`` parameter must still synthesize,
        and the audio produced that way must be cached under 1.0x -- never under
        the speed that was asked for."""
        import db.database as database

        calls = []

        def legacy_kokoro(text, voice="af_heart"):
            calls.append((text, voice))
            yield (None, None, np.ones(2400, dtype=np.float32))

        engine = TTSEngine(legacy_kokoro)
        job = SynthJob(sentence_index=0, text="Test", speed=1.5)

        chunks = [chunk async for chunk in engine.stream_job(job)]

        assert chunks, "the fallback call must still produce audio"
        assert calls == [("Test", "af_heart")]
        assert engine._speed_kwarg_supported is False
        meta = engine._sentence_meta[0]
        assert meta["requested_speed"] == 1.5
        assert meta["effective_speed"] == 1.0

        with Session(database.engine) as session:
            assert session.get(AudioCache, engine._cache_key("Test", "af_heart", 1.0)) is not None
            assert session.get(AudioCache, engine._cache_key("Test", "af_heart", 1.5)) is None, (
                "1.0x audio must not be stored under the 1.5x key"
            )


# ---------------------------------------------------------------------------
# Shared helper: stream_job iterates result.tokens so mocks must support it
# ---------------------------------------------------------------------------

class _KResult:
    """Minimal KPipeline.Result stub: supports result[-1] for audio and result.tokens."""
    def __init__(self, audio, tokens=None):
        self._data = (None, None, audio)
        self.tokens = tokens
    def __getitem__(self, idx):
        return self._data[idx]
    def __iter__(self):
        return iter(self._data)


# ---------------------------------------------------------------------------
# Fix 5 — cache-hit JSON parse regression
# ---------------------------------------------------------------------------

class TestCacheHitJsonParse:
    @pytest.mark.asyncio
    async def test_cache_hit_parses_word_timestamps_from_json(self):
        """Regression: stream_job must json.loads word_timestamps from cache.
        Without 'import json', the second run would raise NameError."""
        import json
        import numpy as np
        from unittest.mock import patch, MagicMock
        from db.models import AudioCache

        sample_rate = 24000
        mock_audio = np.ones(2400, dtype=np.float32)
        pcm = (mock_audio * 32768.0).clip(-32768, 32767).astype(np.int16).tobytes()
        word_ts_json = json.dumps([{"word": "hello", "start": 0.0, "end": 0.05}])

        cached_entry = MagicMock(spec=AudioCache)
        cached_entry.audio_data = pcm
        cached_entry.word_timestamps = word_ts_json
        cached_entry.duration_ms = 100

        mock_session = MagicMock()
        # First run: cache miss → synthesize → pre-save check → save
        # Second run: cache hit → json.loads
        mock_session.get.side_effect = [None, None, cached_entry]
        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__.return_value = mock_session
        mock_session_ctx.__exit__.return_value = None

        def mock_kokoro(text, voice, speed):
            yield _KResult(mock_audio)

        with patch('services.tts_engine.Session', return_value=mock_session_ctx):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="hello world", voice="af_heart", speed=1.0)

            async def drain():
                async for _ in engine.stream_job(job):
                    pass

            await drain()                    # first run: synthesize + cache
            engine._sentence_meta.clear()
            await drain()                    # second run: cache hit → json.loads

        meta = engine._sentence_meta.get(0, {})
        assert isinstance(meta.get("word_timestamps"), list), \
            "word_timestamps must be a list after json.loads from cache"
        assert meta["word_timestamps"][0]["word"] == "hello"


# ---------------------------------------------------------------------------
# Fix 6 — speed regression: parametrized tests
# ---------------------------------------------------------------------------

class TestSpeedParametrized:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("speed", [1.0, 1.5, 2.0, 3.0])
    async def test_stream_job_passes_speed_to_kokoro(self, speed):
        """speed kwarg must reach KPipeline.__call__ unchanged at every supported speed."""
        import numpy as np
        from unittest.mock import patch, MagicMock

        seen: dict = {}

        def spy_kokoro(text, voice, speed):
            seen["speed"] = speed
            yield _KResult(np.ones(2400, dtype=np.float32))

        mock_session = MagicMock()
        mock_session.get.return_value = None  # always cache miss
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_session
        mock_ctx.__exit__.return_value = None

        with patch('services.tts_engine.Session', return_value=mock_ctx):
            engine = TTSEngine(kokoro=spy_kokoro)
            job = SynthJob(sentence_index=0, text="hello", voice="af_heart", speed=speed)
            async for _ in engine.stream_job(job):
                pass

        assert seen.get("speed") == speed, \
            f"Expected speed={speed} to reach KPipeline, got {seen.get('speed')}"

    @pytest.mark.parametrize("speed_a,speed_b", [(1.0, 1.5), (1.5, 2.0), (2.0, 3.0)])
    def test_cache_keys_differ_between_speeds(self, speed_a, speed_b):
        """Different speeds must produce separate cache entries."""
        engine = TTSEngine(kokoro=None)
        assert engine._cache_key("hello", "af_heart", speed_a) != \
               engine._cache_key("hello", "af_heart", speed_b), \
               f"Cache keys must differ for speed {speed_a} vs {speed_b}"

    def test_cache_key_stable_for_same_speed(self):
        engine = TTSEngine(kokoro=None)
        assert engine._cache_key("hello", "af_heart", 2.0) == \
               engine._cache_key("hello", "af_heart", 2.0)
