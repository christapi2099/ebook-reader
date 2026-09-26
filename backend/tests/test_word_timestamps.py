"""Tests for word-level timestamp extraction and storage in AudioCache.

Every test here runs against a real SQLite database: the autouse guard in
conftest.py points ``db.database.engine`` at a throwaway in-memory engine for the
engine tests and at a per-test temp file for the WebSocket test. The previous
version of this file patched ``db.database.Session`` (a no-op -- ``tts_engine``
does ``from sqlmodel import Session`` into its own namespace), built
``AudioCache`` rows it never inserted, and patched ``dict.pop`` process-wide, so
none of it touched the code it claimed to test.
"""
import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import patch

import numpy as np
import pytest
from sqlmodel import Session

import db.database as database
from db.models import AudioCache
from services.tts_engine import TTSEngine, SynthJob

SENTENCE_SAMPLES = 2400  # 100 ms at 24 kHz, i.e. exactly one streamed chunk


class _Token:
    """Stand-in for a Kokoro result token."""

    def __init__(self, text, phonemes="p", whitespace=" ", start_ts=0.0, end_ts=0.5):
        self.text = text
        self.phonemes = phonemes
        self.whitespace = whitespace
        self.start_ts = start_ts
        self.end_ts = end_ts


class _Result:
    """Minimal KPipeline result: ``result[-1]`` is the audio, ``result.tokens`` the words."""

    def __init__(self, audio, tokens=None):
        self._data = (None, None, audio)
        self.tokens = tokens

    def __getitem__(self, index):
        return self._data[index]


def _kokoro_with_tokens(tokens, samples=SENTENCE_SAMPLES, calls=None):
    """A fake pipeline that yields one result carrying `tokens`."""

    def kokoro(text, voice="af_heart", speed=1.0):
        if calls is not None:
            calls.append(text)
        yield _Result(np.ones(samples, dtype=np.float32), tokens=list(tokens))

    return kokoro


async def _drain(engine, job):
    """Collect every chunk stream_job yields for `job`."""
    chunks = []
    async for chunk in engine.stream_job(job):
        chunks.append(chunk)
    return chunks


def _cached_row(text, voice="af_heart", speed=1.0, word_timestamps=None, duration_ms=100):
    """Insert an AudioCache row the way a previous run would have, and return its key."""
    key = TTSEngine(kokoro=None)._cache_key(text, voice, speed)
    with Session(database.engine) as session:
        session.add(
            AudioCache(
                text_hash=key,
                audio_data=np.ones(SENTENCE_SAMPLES, dtype=np.int16).tobytes(),
                duration_ms=duration_ms,
                voice=voice,
                word_timestamps=word_timestamps,
                created_at=datetime.now(UTC),
            )
        )
        session.commit()
    return key


def test_extract_word_timestamps_from_kokoro():
    """stream_job stores per-word start/end times taken from Kokoro's tokens."""
    tokens = [
        _Token("Hello", "həˈloʊ", " ", 0.25, 0.325),
        _Token("world", "wˈɜːld", "", 0.325, 0.725),
    ]
    engine = TTSEngine(_kokoro_with_tokens(tokens))
    job = SynthJob(sentence_index=0, text="Hello world", voice="af_heart", speed=1.0)

    asyncio.run(_drain(engine, job))

    meta = engine._sentence_meta[0]
    assert meta["duration_ms"] == 100
    assert meta["word_timestamps"] == [
        {"word": "Hello", "start": 0.25, "end": 0.325},
        {"word": "world", "start": 0.325, "end": 0.725},
    ]


def test_non_spoken_tokens_are_not_timestamps():
    """Punctuation and phoneme-less tokens must not become highlightable words."""
    tokens = [
        _Token("Hello", "həˈloʊ", " ", 0.0, 0.1),
        _Token(".", ".", "", 0.1, 0.12),
        _Token(" ", "", " ", 0.12, 0.14),
        _Token("world", "", " ", 0.14, 0.3),
    ]
    engine = TTSEngine(_kokoro_with_tokens(tokens))

    asyncio.run(_drain(engine, SynthJob(sentence_index=0, text="Hello. world")))

    assert [w["word"] for w in engine._sentence_meta[0]["word_timestamps"]] == ["Hello"]


def test_word_timestamps_stored_in_cache():
    """A cache-miss synthesis stores the timestamps alongside the audio."""
    engine = TTSEngine(_kokoro_with_tokens([_Token("Test", "tˈɛst", "", 0.1, 0.5)]))
    job = SynthJob(sentence_index=0, text="Test", voice="af_heart", speed=1.0)

    asyncio.run(_drain(engine, job))

    key = engine._cache_key(job.text, job.voice, job.speed)
    with Session(database.engine) as session:
        entry = session.get(AudioCache, key)
        assert entry is not None, "a cache miss must create an AudioCache row"
        assert entry.duration_ms == 100
        stored = json.loads(entry.word_timestamps)
    assert stored == [{"word": "Test", "start": 0.1, "end": 0.5}]


def test_word_timestamps_from_cache_hit():
    """A cache hit replays the stored timestamps without re-synthesising."""
    cached_ts = [
        {"word": "Cached", "start": 0.0, "end": 0.5},
        {"word": "word", "start": 0.5, "end": 1.0},
    ]
    key = _cached_row("Cached word", word_timestamps=json.dumps(cached_ts))

    calls: list[str] = []
    engine = TTSEngine(_kokoro_with_tokens([], calls=calls))
    job = SynthJob(sentence_index=0, text="Cached word", voice="af_heart", speed=1.0)

    chunks = asyncio.run(_drain(engine, job))

    assert chunks, "a cache hit must still stream audio"
    assert calls == [], "a cache hit must not re-synthesise"
    assert engine._sentence_meta[0]["word_timestamps"] == cached_ts
    assert engine._sentence_meta[0]["duration_ms"] == 100
    assert key == engine._cache_key(job.text, job.voice, job.speed)


def test_proportional_fallback_for_null_timestamps():
    """Legacy rows with NULL timestamps fall back to the g2p estimate."""
    _cached_row("Legacy text", word_timestamps=None, duration_ms=1000)
    proportional = [
        {"word": "Legacy", "start": 0.2, "end": 0.4},
        {"word": "text", "start": 0.4, "end": 0.8},
    ]

    calls: list[str] = []
    engine = TTSEngine(_kokoro_with_tokens([], calls=calls))
    job = SynthJob(sentence_index=0, text="Legacy text", voice="af_heart", speed=1.0)

    with patch.object(engine, "_proportional_timestamps", return_value=proportional) as fallback:
        asyncio.run(_drain(engine, job))

    fallback.assert_called_once_with("Legacy text", 1000)
    assert calls == []
    assert engine._sentence_meta[0]["word_timestamps"] == proportional


@pytest.mark.xfail(
    reason=(
        "PRODUCTION BUG (services/tts_engine.py, not fixed here): _write_cache_entry "
        "stores `word_timestamps=json.dumps(word_timestamps) if word_timestamps else None`, "
        "so a synthesis that produced NO tokens is cached as NULL -- indistinguishable "
        "from a legacy row. stream_job's cache-hit path then calls _proportional_timestamps, "
        "which builds a misaki G2P (spaCy) ON THE EVENT LOOP; in a process where "
        "transformers is not yet imported that drags spacy_curated_transformers and "
        "transformers in, freezing the whole server (every WebSocket and /health) for "
        "tens of seconds. It also means the first playback of such a sentence reports no "
        "word timestamps and every replay reports estimated ones."
    ),
    strict=False,
)
def test_synthesis_without_tokens_is_cached_as_empty_not_null():
    """A row synthesized without tokens must be distinguishable from a legacy row."""
    engine = TTSEngine(_kokoro_with_tokens([]))
    job = SynthJob(sentence_index=0, text="No tokens here.", voice="af_heart", speed=1.0)

    asyncio.run(_drain(engine, job))

    with Session(database.engine) as session:
        entry = session.get(AudioCache, engine._cache_key(job.text, job.voice, job.speed))
        assert entry is not None
        assert entry.word_timestamps is not None, "an empty token list must not be stored as NULL"
        assert json.loads(entry.word_timestamps) == []


def test_sentence_end_message_carries_word_timestamps(ws_client_factory, monkeypatch, ws_read):
    """End to end: the timestamps reach the client on the sentence_end message."""
    import routers.tts as tts_router

    tokens = [
        _Token("Hello", "həˈloʊ", " ", 0.25, 0.325),
        _Token("world", "wˈɜːld", "", 0.325, 0.725),
    ]
    with ws_client_factory(sentences=[{"index": 0, "text": "Hello world."}]) as client:
        # The handler reads the module global when the socket connects.
        monkeypatch.setattr(tts_router, "_kokoro", _kokoro_with_tokens(tokens))
        with client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(
                {
                    "action": "play",
                    "from_index": 0,
                    "voice": "af_heart",
                    "speed": 1.0,
                    "session_id": 1,
                }
            )
            messages = ws_read(ws, until=("complete",))

    ends = [
        m["data"]
        for m in messages
        if m["channel"] == "text" and m["data"]["type"] == "sentence_end"
    ]
    assert len(ends) == 1
    assert ends[0]["duration_ms"] == 100
    assert ends[0]["word_timestamps"] == [
        {"word": "Hello", "start": 0.25, "end": 0.325},
        {"word": "world", "start": 0.325, "end": 0.725},
    ]
