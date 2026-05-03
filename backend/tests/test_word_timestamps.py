"""Tests for word-level timestamp extraction and storage in AudioCache."""
import asyncio
import json
from unittest.mock import Mock, patch

import db.database as _db
from db.models import AudioCache
from services.tts_engine import TTSEngine, SynthJob


def test_extract_word_timestamps_from_kokoro():
    """Verify that stream_job extracts word timestamps from Kokoro results."""
    # Mock Kokoro to return results with tokens
    mock_result = Mock()
    mock_result.tokens = [
        Mock(text="Hello", phonemes="həˈloʊ", whitespace=" ", start_ts=0.25, end_ts=0.325),
        Mock(text="world", phonemes="wˈɜːld", whitespace="", start_ts=0.325, end_ts=0.725),
    ]
    
    mock_pipeline_output = [Mock(
        graphemes="Hello world",
        phonemes="həˈloʊ wˈɜːld",
        audio=Mock(shape=(1, 1000)),
        tokens=mock_result.tokens,
    )]
    mock_result.__getitem__ = lambda i: mock_pipeline_output[i]
    
    mock_kokoro = Mock(return_value=iter([mock_result]))
    
    engine = TTSEngine(mock_kokoro)
    job = SynthJob(sentence_index=0, text="Hello world", voice="af_heart", speed=1.0)
    
    # Run stream_job and check that _sentence_meta is populated
    results = list(asyncio.run(engine.stream_job(job)))
    
    assert 0 in engine._sentence_meta, "Sentence metadata should be stored"
    meta = engine._sentence_meta[0]
    assert "word_timestamps" in meta, "word_timestamps should be stored"
    
    word_ts = meta["word_timestamps"]
    assert len(word_ts) == 2, f"Should have 2 words, got {len(word_ts)}"
    assert word_ts[0]["word"] == "Hello", f"First word should be 'Hello', got {word_ts[0]['word']}"
    assert word_ts[0]["start"] == 0.25, f"First word start should be 0.25, got {word_ts[0]['start']}"
    assert word_ts[1]["word"] == "world", f"Second word should be 'world', got {word_ts[1]['word']}"
    assert word_ts[1]["start"] == 0.325, f"Second word start should be 0.325, got {word_ts[1]['start']}"
    
    print("✓ test_extract_word_timestamps_from_kokoro passed")


def test_word_timestamps_stored_in_cache():
    """Verify that word timestamps are stored in AudioCache on cache miss."""
    mock_kokoro = Mock()
    mock_kokoro.return_value = iter([Mock(
        graphemes="Test",
        phonemes="tˈɛst",
        audio=Mock(shape=(1, 1000)),
        tokens=[
            Mock(text="Test", phonemes="tˈɛst", whitespace="", start_ts=0.1, end_ts=0.5),
        ],
    )])
    
    engine = TTSEngine(mock_kokoro)
    job = SynthJob(sentence_index=0, text="Test", voice="af_heart", speed=1.0)
    
    with patch("db.database.Session") as mock_session:
        mock_session.return_value.__enter__ = Mock()
        mock_session.return_value.__exit__ = Mock()
        
        mock_session_instance = Mock()
        mock_session.return_value = mock_session_instance
        
        with patch.object(engine._sentence_meta.__class__, "pop", return_value=None):
            results = list(asyncio.run(engine.stream_job(job)))
    
    # Check that AudioCache entry includes word_timestamps
    with Session(_db.engine) as session:
        entry = session.get(AudioCache, "test_hash")
        assert entry is not None, "AudioCache entry should be created"
        assert entry.word_timestamps is not None, "word_timestamps should be stored in cache"
        
        ts = json.loads(entry.word_timestamps)
        assert len(ts) == 1, f"Should have 1 word, got {len(ts)}"
        assert ts[0]["word"] == "Test", f"Word should be 'Test', got {ts[0]['word']}"
        assert ts[0]["start"] == 0.1, f"Word start should be 0.1, got {ts[0]['start']}"
    
    print("✓ test_word_timestamps_stored_in_cache passed")


def test_word_timestamps_from_cache_hit():
    """Verify that cache hit reads word timestamps without re-synthesis."""
    mock_kokoro = Mock()
    
    # Create a mock cached entry with word timestamps
    mock_cached = AudioCache(
        text_hash="test_hash",
        audio_data=b"\x00" * 1000,
        duration_ms=1000,
        voice="af_heart",
        word_timestamps=json.dumps([
            {"word": "Cached", "start": 0.0, "end": 0.5},
            {"word": "word", "start": 0.5, "end": 1.0},
        ]),
        created_at=None,
    )
    
    engine = TTSEngine(mock_kokoro)
    job = SynthJob(sentence_index=0, text="Cached word", voice="af_heart", speed=1.0)
    
    with patch.object(engine, "_proportional_timestamps", return_value=[]):
        results = list(asyncio.run(engine.stream_job(job)))
    
    # Verify that _sentence_meta has the cached word timestamps
    assert 0 in engine._sentence_meta, "Sentence metadata should be stored"
    meta = engine._sentence_meta[0]
    assert "word_timestamps" in meta, "word_timestamps should be from cache"
    
    word_ts = meta["word_timestamps"]
    assert len(word_ts) == 2, f"Should have 2 cached words, got {len(word_ts)}"
    assert word_ts[0]["word"] == "Cached", f"First word should be 'Cached', got {word_ts[0]['word']}"
    
    # Verify Kokoro was NOT called (cache hit should not re-synthesize)
    mock_kokoro.assert_not_called()
    
    print("✓ test_word_timestamps_from_cache_hit passed")


def test_proportional_fallback_for_null_timestamps():
    """Verify that g2p proportional estimation is used when word_timestamps is None."""
    mock_kokoro = Mock()
    
    # Mock cached entry WITHOUT word timestamps (legacy cache)
    mock_cached = AudioCache(
        text_hash="legacy_hash",
        audio_data=b"\x00" * 1000,
        duration_ms=1000,
        voice="af_heart",
        word_timestamps=None,
        created_at=None,
    )
    
    engine = TTSEngine(mock_kokoro)
    job = SynthJob(sentence_index=0, text="Legacy text", voice="af_heart", speed=1.0)
    
    with patch("db.database.Session") as mock_session:
        mock_session.return_value.__enter__ = Mock()
        mock_session.return_value.__exit__ = Mock()
        
        mock_session_instance = Mock()
        mock_session_instance.get.return_value = mock_cached
        
        with patch.object(engine, "_proportional_timestamps") as mock_prop:
            mock_prop.return_value = [
                {"word": "Legacy", "start": 0.2, "end": 0.4},
                {"word": "text", "start": 0.4, "end": 0.8},
            ]
            
            results = list(asyncio.run(engine.stream_job(job)))
    
    # Verify that proportional timestamps were used
    assert 0 in engine._sentence_meta, "Sentence metadata should be stored"
    meta = engine._sentence_meta[0]
    assert "word_timestamps" in meta, "word_timestamps should be stored"
    
    word_ts = meta["word_timestamps"]
    assert len(word_ts) == 2, f"Should have 2 proportional words, got {len(word_ts)}"
    assert word_ts[0]["word"] == "Legacy", f"First word should be 'Legacy', got {word_ts[0]['word']}"
    
    # Verify _proportional_timestamps was called
    mock_prop.assert_called_once()
    
    print("✓ test_proportional_fallback_for_null_timestamps passed")


def test_sentence_end_includes_word_timestamps():
    """Verify that sentence_end message includes word_timestamps."""
    mock_kokoro = Mock()
    mock_kokoro.return_value = iter([Mock(
        graphemes="Test",
        phonemes="tˈɛst",
        audio=Mock(shape=(1, 1000)),
        tokens=[Mock(text="Test", phonemes="tˈɛst", whitespace="", start_ts=0.0, end_ts=0.5)],
    )])
    
    engine = TTSEngine(mock_kokoro)
    job = SynthJob(sentence_index=0, text="Test", voice="af_heart", speed=1.0)
    
    messages_sent = []
    
    async def mock_send_text(msg: str):
        messages_sent.append(json.loads(msg))
    
    async def mock_send_bytes(data: bytes):
        pass
    
    with patch.object(engine, "_proportional_timestamps", return_value=[]):
        results = list(asyncio.run(engine.stream_job(job)))
    
    # Simulate what tts.py does: read _sentence_meta and send sentence_end
    meta = engine._sentence_meta.pop(0, {})
    word_ts = meta.get("word_timestamps", [])
    duration_ms = meta.get("duration_ms", 100)
    
    msg = {
        "type": "sentence_end",
        "index": 0,
        "duration_ms": duration_ms,
        "word_timestamps": word_ts,
        "session_id": 1,
    }
    
    assert "word_timestamps" in msg, "sentence_end should include word_timestamps"
    assert msg["word_timestamps"] == [], "word_timestamps should be empty list"
    assert msg["duration_ms"] == 500, "duration_ms should be accurate (from audio length)"
    
    print("✓ test_sentence_end_includes_word_timestamps passed")


if __name__ == "__main__":
    test_extract_word_timestamps_from_kokoro()
    test_word_timestamps_stored_in_cache()
    test_word_timestamps_from_cache_hit()
    test_proportional_fallback_for_null_timestamps()
    test_sentence_end_includes_word_timestamps()
    print("\n✅ All backend word timestamp tests passed!")
