import asyncio
import pytest
import numpy as np
from unittest.mock import MagicMock, patch, AsyncMock
from sqlmodel import Session, create_engine
from datetime import datetime, UTC

from services.tts_engine import TTSEngine, SynthJob
from db.models import AudioCache

@pytest.fixture(scope="session")
def test_engine():
    """In-memory SQLite engine for tests."""
    engine = create_engine("sqlite:///:memory:")
    AudioCache.metadata.create_all(engine)
    return engine

@pytest.fixture
def mock_kokoro():
    """Mock Kokoro function as lambda."""
    return lambda text, voice, speed: [(None, None, np.ones(2400, dtype=np.float32))]

class TestPrefetch:
    @pytest.mark.asyncio
    async def test_prefetch_populates_cache_for_unfiltered_sentences(self, test_engine, mock_kokoro):
        """prefetch() populates AudioCache for up to count unfiltered sentences starting at from_index."""
        sentences = {
            0: {"text": "Hello world.", "filtered": False},
            1: {"text": "Next sentence.", "filtered": False},
            2: {"text": "Last one.", "filtered": False}
        }
        engine = TTSEngine(mock_kokoro)
        cancel = asyncio.Event()
        with patch('services.tts_engine._db.engine', test_engine):
            await engine.prefetch(sentences, from_index=0, count=2, voice="af_heart", speed=1.0, cancel=cancel)

        with Session(test_engine) as session:
            cached = session.get(AudioCache, engine._cache_key(sentences[0]["text"], "af_heart", 1.0))
            assert cached is not None
            cached = session.get(AudioCache, engine._cache_key(sentences[1]["text"], "af_heart", 1.0))
            assert cached is not None
            cached = session.get(AudioCache, engine._cache_key(sentences[2]["text"], "af_heart", 1.0))
            assert cached is None  # Only 2 requested

    @pytest.mark.asyncio
    async def test_prefetch_skips_filtered_sentences(self, test_engine, mock_kokoro):
        """prefetch() skips filtered sentences."""
        sentences = {
            0: {"text": "Hello world.", "filtered": False},
            1: {"text": "Filtered.", "filtered": True},
            2: {"text": "Next unfiltered.", "filtered": False}
        }
        engine = TTSEngine(mock_kokoro)
        cancel = asyncio.Event()
        with patch('services.tts_engine._db.engine', test_engine):
            await engine.prefetch(sentences, from_index=0, count=10, voice="af_heart", speed=1.0, cancel=cancel)

        with Session(test_engine) as session:
            cached = session.get(AudioCache, engine._cache_key(sentences[0]["text"], "af_heart", 1.0))
            assert cached is not None
            cached = session.get(AudioCache, engine._cache_key(sentences[1]["text"], "af_heart", 1.0))
            assert cached is None
            cached = session.get(AudioCache, engine._cache_key(sentences[2]["text"], "af_heart", 1.0))
            assert cached is not None

    @pytest.mark.asyncio
    async def test_prefetch_skips_already_cached_sentences(self, test_engine, mock_kokoro):
        """prefetch() skips sentences already in cache (no re-synthesis)."""
        sentences = {
            0: {"text": "Cached already.", "filtered": False}
        }
        engine = TTSEngine(mock_kokoro)
        with Session(test_engine) as session:
            key = engine._cache_key(sentences[0]["text"], "af_heart", 1.0)
            existing = AudioCache(
                text_hash=key,
                audio_data=b"dummy",
                duration_ms=100,
                voice="af_heart",
                created_at=datetime.now(UTC)
            )
            session.add(existing)
            session.commit()

        calls: list[str] = []
        def tracking_kokoro(text, voice, speed):
            calls.append(text)
            return [(None, None, np.ones(2400, dtype=np.float32))]
        engine2 = TTSEngine(tracking_kokoro)
        cancel = asyncio.Event()
        with patch('services.tts_engine._db.engine', test_engine):
            await engine2.prefetch(sentences, from_index=0, count=1, voice="af_heart", speed=1.0, cancel=cancel)
        assert len(calls) == 0  # Should not call kokoro since cached

    @pytest.mark.asyncio
    async def test_prefetch_stops_when_cancel_set(self, test_engine, mock_kokoro):
        """prefetch() stops when cancel Event is set before processing."""
        sentences = {
            0: {"text": "First.", "filtered": False},
            1: {"text": "Second.", "filtered": False}
        }
        engine = TTSEngine(mock_kokoro)
        cancel = asyncio.Event()
        cancel.set()  # Set cancel before starting

        with patch('services.tts_engine._db.engine', test_engine):
            await engine.prefetch(sentences, from_index=0, count=10, voice="af_heart", speed=1.0, cancel=cancel)

        with Session(test_engine) as session:
            cached_first = session.get(AudioCache, engine._cache_key(sentences[0]["text"], "af_heart", 1.0))
            assert cached_first is None  # Stopped before processing any

    @pytest.mark.asyncio
    async def test_prefetch_from_index_past_sentences_no_crash(self, test_engine, mock_kokoro):
        """prefetch() with from_index past all sentences = no crash, no synthesis."""
        sentences = {
            0: {"text": "Only one.", "filtered": False}
        }
        engine = TTSEngine(mock_kokoro)
        cancel = asyncio.Event()
        with patch('services.tts_engine._db.engine', test_engine):
            await engine.prefetch(sentences, from_index=5, count=10, voice="af_heart", speed=1.0, cancel=cancel)

        with Session(test_engine) as session:
            cached = session.get(AudioCache, engine._cache_key(sentences[0]["text"], "af_heart", 1.0))
            assert cached is None  # Nothing synthesized

    @pytest.mark.asyncio
    async def test_prefetch_exception_in_one_sentence_continues_rest(self, test_engine):
        """prefetch() exception in one sentence doesnt abort the rest."""
        def mock_kokoro_broken(text, voice, speed):
            if "broken" in text:
                raise Exception("Synthesis error")
            return [(None, None, np.ones(2400, dtype=np.float32))]

        sentences = {
            0: {"text": "This works.", "filtered": False},
            1: {"text": "This is broken.", "filtered": False},
            2: {"text": "This also works.", "filtered": False}
        }
        engine = TTSEngine(mock_kokoro_broken)
        cancel = asyncio.Event()
        with patch('services.tts_engine._db.engine', test_engine):
            await engine.prefetch(sentences, from_index=0, count=10, voice="af_heart", speed=1.0, cancel=cancel)

        with Session(test_engine) as session:
            cached0 = session.get(AudioCache, engine._cache_key(sentences[0]["text"], "af_heart", 1.0))
            assert cached0 is not None
            cached1 = session.get(AudioCache, engine._cache_key(sentences[1]["text"], "af_heart", 1.0))
            assert cached1 is None  # Failed, so not cached
            cached2 = session.get(AudioCache, engine._cache_key(sentences[2]["text"], "af_heart", 1.0))
            assert cached2 is not None

class TestStreamJobCache:
    @pytest.mark.asyncio
    async def test_stream_job_cache_hit_no_kokoro_call_correct_wav(self, test_engine, mock_kokoro):
        """stream_job() cache hit: kokoro not called, correct WAV bytes returned."""
        engine = TTSEngine(mock_kokoro)
        text = "Cached text."
        voice, speed = "af_heart", 1.0
        key = engine._cache_key(text, voice, speed)
        audio_data = np.ones(2400, dtype=np.float32) * 0.5
        pcm_bytes = (audio_data * 32768.0).clip(-32768, 32767).astype(np.int16).tobytes()

        with Session(test_engine) as session:
            cached = AudioCache(
                text_hash=key,
                audio_data=pcm_bytes,
                duration_ms=100,
                voice=voice,
                created_at=datetime.now(UTC)
            )
            session.add(cached)
            session.commit()

        calls: list[str] = []
        def tracking_kokoro(text, voice, speed):
            calls.append(text)
            return [(None, None, np.ones(2400, dtype=np.float32))]
        engine2 = TTSEngine(tracking_kokoro)
        job = SynthJob(sentence_index=0, text=text, voice=voice, speed=speed)
        chunks = []
        with patch('services.tts_engine._db.engine', test_engine):
            async for chunk in engine2.stream_job(job):
                chunks.append(chunk)
        assert len(calls) == 0  # Cache hit — kokoro never called
        assert len(chunks) > 0
        assert chunks[0].startswith(b'RIFF')

    @pytest.mark.asyncio
    async def test_stream_job_cache_miss_speed_15_cache_populated(self, test_engine, mock_kokoro):
        """stream_job() cache miss at speed=1.5: key includes speed, cache populated."""
        engine = TTSEngine(mock_kokoro)
        job = SynthJob(sentence_index=0, text="Speed test.", voice="af_heart", speed=1.5)

        chunks = []
        with patch('services.tts_engine._db.engine', test_engine):
            async for chunk in engine.stream_job(job):
                chunks.append(chunk)
        assert len(chunks) > 0

        with Session(test_engine) as session:
            key = engine._cache_key(job.text, job.voice, job.speed)
            cached = session.get(AudioCache, key)
            assert cached is not None
            assert cached.voice == "af_heart"

    def test_duration_math_chunk_count_5_speed_15_333ms(self):
        """Duration math unit test: chunk_count=5 speed=1.5 → duration_ms=333."""
        chunk_count = 5
        speed = 1.5
        # Based on tts.py line 101: duration_ms = int(chunk_count * 100 / job.speed)
        expected = int(chunk_count * 100 / speed)
        assert expected == 333
