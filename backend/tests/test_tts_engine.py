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


class TestStreamNext:
    @pytest.mark.asyncio
    async def test_skips_cancelled_job(self):
        from unittest.mock import patch, MagicMock
        mock_kokoro = MagicMock()
        mock_kokoro.return_value = [("g", "p", MagicMock())]
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
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
        from unittest.mock import patch, MagicMock
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro = MagicMock(return_value=[("graphemes", "phonemes", mock_audio)])
        mock_session = MagicMock()
        mock_session.get.return_value = None  # cache miss
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="Hello world this is a test.")
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            assert len(chunks) > 0
            assert all(isinstance(c, bytes) for c in chunks)


class TestAudioCacheIntegration:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_kokoro(self):
        import numpy as np
        from unittest.mock import patch, MagicMock, call
        mock_kokoro = MagicMock()
        mock_audio = np.zeros(24000, dtype=np.float32)
        cached_entry = MagicMock()
        cached_entry.audio_data = (mock_audio * 32768).astype(np.int16).tobytes()
        mock_session = MagicMock()
        mock_session.get.return_value = cached_entry
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="Cached.")
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            mock_kokoro.assert_not_called()
            assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_cache_miss_calls_kokoro(self):
        import numpy as np
        from unittest.mock import patch, MagicMock, call
        mock_kokoro = MagicMock()
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro.return_value = [("g", "p", mock_audio)]
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="Uncached.")
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            mock_kokoro.assert_called_once_with(job.text, voice=job.voice, speed=job.speed)
            assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_cache_write_after_synthesis(self):
        import numpy as np
        from unittest.mock import patch, MagicMock, call
        mock_kokoro = MagicMock()
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro.return_value = [("g", "p", mock_audio)]
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock()
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="To cache.")
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            mock_session.add.assert_called_once()
            call_arg = mock_session.add.call_args[0][0]
            assert call_arg.text_hash == engine._cache_key(job.text, job.voice, job.speed)
            assert call_arg.audio_data is not None
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_write_failure_does_not_break_synthesis(self):
        import numpy as np
        from unittest.mock import patch, MagicMock
        mock_kokoro = MagicMock()
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro.return_value = [("g", "p", mock_audio)]
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=Exception("DB error"))
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="Cache write fails.")
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            assert len(chunks) > 0
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit_yields_correct_chunks(self):
        import numpy as np
        import io
        import soundfile as sf
        from unittest.mock import patch, MagicMock
        sample_rate = 24000
        duration = 0.1
        samples = int(sample_rate * duration)
        audio = np.sin(2 * np.pi * 440 * np.arange(samples) / sample_rate).astype(np.float32)
        pcm_bytes = (audio * 32768).astype(np.int16).tobytes()
        cached_entry = MagicMock()
        cached_entry.audio_data = pcm_bytes
        mock_session = MagicMock()
        mock_session.get.return_value = cached_entry
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=MagicMock())
            job = SynthJob(sentence_index=0, text="Cached audio.")
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            for chunk in chunks:
                buf = io.BytesIO(chunk)
                data, sr = sf.read(buf)
                assert sr == sample_rate
                assert len(data) > 0


class TestKokoroSpeedParam:
    @pytest.mark.asyncio
    async def test_speed_passed_to_kokoro(self):
        import numpy as np
        from unittest.mock import patch, MagicMock
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro = MagicMock(return_value=[("g", "p", mock_audio)])
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="Test", speed=1.5)
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            mock_kokoro.assert_called_once_with(job.text, voice=job.voice, speed=1.5)

    @pytest.mark.asyncio
    async def test_speed_1x_default(self):
        import numpy as np
        from unittest.mock import patch, MagicMock
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro = MagicMock(return_value=[("g", "p", mock_audio)])
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="Test")
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            mock_kokoro.assert_called_once_with(job.text, voice=job.voice, speed=1.0)

    @pytest.mark.asyncio
    async def test_kokoro_type_error_fallback(self):
        import numpy as np
        from unittest.mock import patch, MagicMock, call
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro = MagicMock()
        # First call raises TypeError (speed param not supported)
        mock_kokoro.side_effect = [
            TypeError("speed argument not supported"),
            [("g", "p", mock_audio)]
        ]
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session_context = MagicMock()
        mock_session_context.__enter__.return_value = mock_session
        mock_session_context.__exit__.return_value = None
        with patch('services.tts_engine.Session', return_value=mock_session_context):
            engine = TTSEngine(kokoro=mock_kokoro)
            job = SynthJob(sentence_index=0, text="Test", speed=1.5)
            await engine.enqueue(job)
            chunks = []
            async for chunk in engine.stream_next():
                chunks.append(chunk)
            # Should have called twice: first with speed, second without speed
            assert mock_kokoro.call_count == 2
            first_call = mock_kokoro.call_args_list[0]
            assert first_call == call(job.text, voice=job.voice, speed=1.5)
            second_call = mock_kokoro.call_args_list[1]
            assert second_call == call(job.text, voice=job.voice)
