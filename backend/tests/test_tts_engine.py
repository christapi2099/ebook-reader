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
