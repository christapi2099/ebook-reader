"""Tests that voice parameter flows through the WebSocket to the TTS engine."""
import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, UTC
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, SQLModel

import db.database as _db_module
import routers.tts as tts_router
from db.models import Book, Sentence


def _build_engine():
    db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    db_file.close()
    engine = create_engine(f"sqlite:///{db_file.name}")
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(engine):
    with Session(engine) as session:
        session.add(Book(
            id="test-book",
            title="Test",
            author="Author",
            file_path="/test.pdf",
            file_type="pdf",
            page_count=1,
            cover_page=0,
            created_at=datetime.now(UTC),
        ))
        session.commit()
        for idx, text in enumerate(["Hello.", "World.", "End."]):
            session.add(Sentence(
                book_id="test-book",
                index=idx,
                text=text,
                page=1,
                x0=0, y0=0, x1=100, y1=20,
                filtered=False,
            ))
        session.commit()


def _make_mock_kokoro(voice_log):
    def mock_kokoro(text, voice, speed):
        voice_log.append(voice)
        audio = np.ones(2400, dtype=np.float32)
        yield (None, None, audio)
    return mock_kokoro


@contextmanager
def _patched_client(engine, voice_log):
    mock_kokoro = _make_mock_kokoro(voice_log)

    _db_module.engine = engine
    original = _db_module.create_engine_and_tables

    def patched_create(db_url=None):
        return engine

    _db_module.create_engine_and_tables = patched_create

    def _set_kokoro_side(val):
        tts_router._kokoro = val

    try:
        with patch("main.create_engine_and_tables", patched_create), \
             patch("main._init_kokoro", return_value=mock_kokoro), \
             patch("routers.tts.set_kokoro", side_effect=_set_kokoro_side):
            from main import app
            with TestClient(app) as client:
                yield client
    finally:
        _db_module.create_engine_and_tables = original


def _drain(ws, max_iters=60):
    for _ in range(max_iters):
        try:
            raw = ws.receive_text()
            data = json.loads(raw)
            if data["type"] == "complete":
                return
        except Exception:
            try:
                ws.receive_bytes()
            except Exception:
                return


class TestVoiceChange:
    def test_play_passes_voice_to_engine(self):
        voice_log: list[str] = []
        engine = _build_engine()
        _seed(engine)

        with _patched_client(engine, voice_log) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json({
                    "action": "play",
                    "from_index": 0,
                    "voice": "bf_emma",
                    "speed": 1.0,
                    "session_id": 1,
                })
                _drain(ws)

        assert len(voice_log) > 0
        assert all(v == "bf_emma" for v in voice_log), f"Expected bf_emma, got {voice_log}"

    def test_voice_switch_mid_session(self):
        voice_log: list[str] = []
        engine = _build_engine()
        _seed(engine)

        with _patched_client(engine, voice_log) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json({
                    "action": "play",
                    "from_index": 0,
                    "voice": "af_heart",
                    "speed": 1.0,
                    "session_id": 1,
                })
                _drain(ws)

                first_voices = list(voice_log)
                voice_log.clear()

                ws.send_json({
                    "action": "play",
                    "from_index": 0,
                    "voice": "am_adam",
                    "speed": 1.0,
                    "session_id": 2,
                })
                _drain(ws)

        assert len(first_voices) > 0
        assert all(v == "af_heart" for v in first_voices)
        assert len(voice_log) > 0
        assert all(v == "am_adam" for v in voice_log), f"Expected am_adam, got {voice_log}"

    def test_seek_passes_voice(self):
        voice_log: list[str] = []
        engine = _build_engine()
        _seed(engine)

        with _patched_client(engine, voice_log) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json({
                    "action": "seek",
                    "to_index": 2,
                    "voice": "af_nicole",
                    "speed": 1.0,
                    "session_id": 5,
                })
                _drain(ws)

        assert len(voice_log) > 0
        assert all(v == "af_nicole" for v in voice_log)

    def test_default_voice_when_omitted(self):
        voice_log: list[str] = []
        engine = _build_engine()
        _seed(engine)

        with _patched_client(engine, voice_log) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json({
                    "action": "play",
                    "from_index": 0,
                    "speed": 1.0,
                    "session_id": 1,
                })
                _drain(ws)

        assert len(voice_log) > 0
        assert all(v == "af_heart" for v in voice_log)
