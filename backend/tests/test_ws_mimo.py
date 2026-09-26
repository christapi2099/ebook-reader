"""WebSocket protocol tests against a minimal app that mounts only the TTS router.

Unlike tests/test_websocket_integration.py -- which drives ``main.app`` including
its lifespan -- these tests prove the handler is self-contained: no database
bootstrap, no Kokoro initialisation, no dependency overrides. That is also how
uvicorn serves it, so the handler must not depend on the application's startup
wiring.

The engine and the fake pipeline come from conftest (``ws_engine``,
``seed_book``, ``fake_kokoro``); ``ws_engine`` swaps ``db.database.engine``
through ``monkeypatch``, so nothing is left pointing at a dead temp database
when the test finishes.
"""
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import tts as tts_router

SENTENCE_SAMPLES = 2400  # 100 ms at 24 kHz
DEFAULT_BOOK = "test-book"


def _play(index=0, *, speed=1.0, session_id=1, action="play"):
    key = "from_index" if action == "play" else "to_index"
    return {"action": action, key: index, "voice": "af_heart", "speed": speed, "session_id": session_id}


def _texts(messages):
    return [m["data"] for m in messages if m["channel"] == "text"]


def _chunks(messages):
    return [m["data"] for m in messages if m["channel"] == "bytes"]


def _kokoro_with(samples_per_result, *, speed_aware=False):
    """Fake pipeline yielding one result per call."""

    def kokoro(text, voice="af_heart", speed=1.0):
        count = samples_per_result
        if speed_aware:
            count = int(round(samples_per_result / (float(speed) or 1.0)))
        yield (None, None, np.ones(max(1, count), dtype=np.float32))

    return kokoro


@pytest.fixture
def mimo_client(ws_engine, seed_book, fake_kokoro, monkeypatch):
    """A TestClient for a router-only app with a seeded three-sentence book."""
    seed_book(ws_engine)
    monkeypatch.setattr(tts_router, "_kokoro", fake_kokoro)

    app = FastAPI()
    app.include_router(tts_router.router)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_play_returns_sentence_start_then_chunks_then_end(mimo_client, ws_read):
    """Ordering: sentence_start -> binary chunks -> sentence_end, per sentence."""
    with mimo_client.websocket_connect(f"/ws/tts/{DEFAULT_BOOK}") as ws:
        ws.send_json(_play(0, session_id=1))
        messages = ws_read(ws, until=("complete",))

    text = _texts(messages)
    assert [m["type"] for m in text] == [
        "sentence_start",
        "sentence_end",
        "sentence_start",
        "sentence_end",
        "sentence_start",
        "sentence_end",
        "complete",
    ]
    assert [m["index"] for m in text if m["type"] == "sentence_start"] == [0, 1, 2]
    assert len(_chunks(messages)) == 3
    # Every sentence_start is followed by its audio before its sentence_end.
    assert messages[0]["channel"] == "text"
    assert messages[1]["channel"] == "bytes"


def test_duration_ms_is_the_real_audio_length(mimo_client, monkeypatch, ws_read):
    """7200 samples is 300 ms at 24 kHz, whatever speed was requested.

    This replaced an assertion of ``int(chunks * 100 / speed)`` == 200, which
    encoded the pre-9eb454a formula. Since 9eb454a the router reports the length
    of the audio ``stream_job`` actually produced (``_sentence_meta``), so the
    correct expectation is derived from the sample count the fake returned.
    """
    monkeypatch.setattr(tts_router, "_kokoro", _kokoro_with(7200))

    with mimo_client.websocket_connect(f"/ws/tts/{DEFAULT_BOOK}") as ws:
        ws.send_json(_play(0, speed=1.5, session_id=2))
        messages = ws_read(ws, until=("sentence_end",))

    assert len(_chunks(messages)) == 3, "7200 samples is three 2400-sample chunks"
    ends = [m for m in _texts(messages) if m["type"] == "sentence_end"]
    assert len(ends) == 1
    assert ends[0]["duration_ms"] == 7200 / 24000 * 1000


def test_duration_scales_with_speed_because_kokoro_returns_shorter_audio(mimo_client, monkeypatch, ws_read):
    """Same requested sentence at two speeds: the reported duration differs by
    exactly the ratio of the audio lengths Kokoro returned."""
    durations = {}
    with mimo_client.websocket_connect(f"/ws/tts/{DEFAULT_BOOK}") as ws:
        for speed in (1.0, 2.0):
            monkeypatch.setattr(tts_router, "_kokoro", _kokoro_with(SENTENCE_SAMPLES, speed_aware=True))
            ws.send_json(_play(0, speed=speed, session_id=int(speed * 10)))
            ends = [
                m
                for m in _texts(ws_read(ws, until=("complete",)))
                if m["type"] == "sentence_end"
            ]
            durations[speed] = [m["duration_ms"] for m in ends]

    assert durations[1.0] == [100, 100, 100]
    assert durations[2.0] == [50, 50, 50]


def test_seek_starts_from_correct_sentence(mimo_client, ws_read):
    with mimo_client.websocket_connect(f"/ws/tts/{DEFAULT_BOOK}") as ws:
        ws.send_json(_play(1, session_id=3, action="seek"))
        messages = ws_read(ws, until=("sentence_start",))

    starts = [m["index"] for m in _texts(messages) if m["type"] == "sentence_start"]
    assert starts == [1]


def test_session_id_matches_in_all_messages(mimo_client, ws_read):
    with mimo_client.websocket_connect(f"/ws/tts/{DEFAULT_BOOK}") as ws:
        ws.send_json(_play(0, session_id=99))
        messages = ws_read(ws, until=("complete",))

    text = _texts(messages)
    assert text[-1]["type"] == "complete"
    assert {m["session_id"] for m in text} == {99}


def test_complete_message_received(mimo_client, ws_read):
    with mimo_client.websocket_connect(f"/ws/tts/{DEFAULT_BOOK}") as ws:
        ws.send_json(_play(0, session_id=4))
        messages = ws_read(ws, until=("complete",))

    kinds = [m["type"] for m in _texts(messages)]
    assert kinds.count("complete") == 1, f"expected exactly one complete, got {kinds}"


def test_play_after_seek_uses_new_session(mimo_client, ws_read):
    """Session 6 supersedes session 5: once 6 starts, no 5 message may follow."""
    with mimo_client.websocket_connect(f"/ws/tts/{DEFAULT_BOOK}") as ws:
        ws.send_json(_play(0, session_id=5, action="seek"))
        ws.send_json(_play(0, session_id=6))
        messages = ws_read(ws, until=("complete",))

    text = _texts(messages)
    assert any(m["session_id"] == 6 for m in text), "session 6 produced nothing"
    first_six = next(i for i, m in enumerate(text) if m["session_id"] == 6)
    stale = [m for m in text[first_six:] if m["session_id"] == 5]
    assert stale == [], f"stale session 5 messages after session 6 started: {stale}"
    assert text[-1] == {"type": "complete", "session_id": 6}


def test_filtered_sentences_not_sent(ws_engine, seed_book, fake_kokoro, monkeypatch, ws_read):
    sentences = [
        {"index": 0, "text": "Visible."},
        {"index": 1, "text": "Filtered out.", "filtered": True},
        {"index": 2, "text": "Also visible."},
    ]
    seed_book(ws_engine, book_id="book-filtered", sentences=sentences)
    monkeypatch.setattr(tts_router, "_kokoro", fake_kokoro)

    app = FastAPI()
    app.include_router(tts_router.router)
    with TestClient(app, raise_server_exceptions=True) as client:
        with client.websocket_connect("/ws/tts/book-filtered") as ws:
            ws.send_json(_play(0, session_id=7))
            messages = ws_read(ws, until=("complete",))

    starts = [m["index"] for m in _texts(messages) if m["type"] == "sentence_start"]
    assert starts == [0, 2], f"filtered index 1 appeared in {starts}"
    assert len(_chunks(messages)) == 2, "no audio may be synthesized for a filtered sentence"
