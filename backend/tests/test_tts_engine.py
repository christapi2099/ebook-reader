"""TDD tests for services/tts_engine.py. Mocks Kokoro so no GPU required."""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from services.tts_engine import TTSEngine, SynthJob


class TestSynthJob:
    def test_has_required_fields(self):
        job = SynthJob(sentence_index=5, text="Hello world.")
        assert job.sentence_index == 5
        assert job.text == "Hello world."
        assert job.voice == "af_heart"  # default voice

    def test_custom_voice(self):
        job = SynthJob(sentence_index=0, text="Hi.", voice="am_adam")
        assert job.voice == "am_adam"


class TestTTSEngineInit:
    def test_initializes_with_defaults(self):
        engine = TTSEngine(kokoro=None)
        assert engine.current_voice == "af_heart"
        assert engine.queue.maxsize == 10

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


class TestCancelFrom:
    @pytest.mark.asyncio
    async def test_cancel_marks_indices(self):
        engine = TTSEngine(kokoro=None)
        for i in range(5):
            await engine.enqueue(SynthJob(sentence_index=i, text=f"Sentence {i}."))
        await engine.cancel_from(sentence_index=2)
        assert 2 in engine.cancelled
        assert 3 in engine.cancelled
        assert 4 in engine.cancelled

    @pytest.mark.asyncio
    async def test_cancel_clears_queue(self):
        engine = TTSEngine(kokoro=None)
        for i in range(5):
            await engine.enqueue(SynthJob(sentence_index=i, text=f"Sentence {i}."))
        await engine.cancel_from(sentence_index=0)
        assert engine.queue.empty()

    @pytest.mark.asyncio
    async def test_cancel_clears_cancelled_set_after_reset(self):
        engine = TTSEngine(kokoro=None)
        await engine.enqueue(SynthJob(sentence_index=0, text="Test."))
        await engine.cancel_from(sentence_index=0)
        await engine.enqueue(SynthJob(sentence_index=5, text="New start."))
        assert engine.queue.qsize() == 1


class TestCacheKey:
    def test_same_text_voice_same_key(self):
        engine = TTSEngine(kokoro=None)
        k1 = engine._cache_key("Hello world.", "af_heart")
        k2 = engine._cache_key("Hello world.", "af_heart")
        assert k1 == k2

    def test_different_text_different_key(self):
        engine = TTSEngine(kokoro=None)
        k1 = engine._cache_key("Hello.", "af_heart")
        k2 = engine._cache_key("Goodbye.", "af_heart")
        assert k1 != k2

    def test_different_voice_different_key(self):
        engine = TTSEngine(kokoro=None)
        k1 = engine._cache_key("Hello.", "af_heart")
        k2 = engine._cache_key("Hello.", "am_adam")
        assert k1 != k2

    def test_key_is_string(self):
        engine = TTSEngine(kokoro=None)
        assert isinstance(engine._cache_key("Test.", "af_heart"), str)


class TestStreamNext:
    @pytest.mark.asyncio
    async def test_skips_cancelled_job(self):
        mock_kokoro = MagicMock()
        mock_kokoro.return_value = [(MagicMock(), 24000)]  # (audio_tensor, sample_rate)
        engine = TTSEngine(kokoro=mock_kokoro)
        job = SynthJob(sentence_index=0, text="Skipped.")
        await engine.enqueue(job)
        engine.cancelled.add(0)
        chunks = []
        async for chunk in engine.stream_next():
            chunks.append(chunk)
        assert chunks == []

    @pytest.mark.asyncio
    async def test_yields_bytes_for_valid_job(self):
        import numpy as np
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro = MagicMock(return_value=[(mock_audio, 24000)])
        engine = TTSEngine(kokoro=mock_kokoro)
        job = SynthJob(sentence_index=0, text="Hello world this is a test.")
        await engine.enqueue(job)
        chunks = []
        async for chunk in engine.stream_next():
            chunks.append(chunk)
        assert len(chunks) > 0
        assert all(isinstance(c, bytes) for c in chunks)
