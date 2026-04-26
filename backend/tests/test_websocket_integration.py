import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, UTC
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlmodel import Session, create_engine, SQLModel

import db.database as _db_module
import routers.tts as tts_router
from db.models import Book, Sentence
from routers.tts import set_kokoro


@pytest.fixture
def mock_kokoro():
    def make_kokoro(text, voice, speed):
        audio = np.ones(2400, dtype=np.float32)
        yield (None, None, audio)

    return make_kokoro


def _build_engine():
    db_file = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    db_path = db_file.name
    db_file.close()
    engine = create_engine(f'sqlite:///{db_path}')
    SQLModel.metadata.create_all(engine)
    return engine


@contextmanager
def _patch_db(eng):
    _db_module.engine = eng
    original_func = _db_module.create_engine_and_tables
    def patched_func(db_url=None):
        return eng
    _db_module.create_engine_and_tables = patched_func

    def _set_kokoro_side_effect(val):
        tts_router._kokoro = val

    try:
        with patch('main.create_engine_and_tables', patched_func), \
             patch('routers.tts.set_kokoro', side_effect=_set_kokoro_side_effect):
            yield
    finally:
        _db_module.create_engine_and_tables = original_func


def _seed_data(engine, sentences_by_index):
    with Session(engine) as session:
        book = Book(
            id="test-book",
            title="Test Book",
            author="Test Author",
            file_path="/test/path.pdf",
            file_type="pdf",
            page_count=1,
            cover_page=0,
            created_at=datetime.now(UTC),
        )
        session.add(book)
        session.commit()
        for idx, text_val in sentences_by_index.items():
            sent = Sentence(
                book_id="test-book",
                index=idx,
                text=text_val,
                page=1,
                x0=0.0,
                y0=0.0,
                x1=100.0,
                y1=20.0,
                filtered=False,
            )
            session.add(sent)
        session.commit()


@pytest.fixture
def in_memory_engine():
    return _build_engine()


@pytest.fixture
def seed_single_sentence(in_memory_engine):
    _seed_data(in_memory_engine, {0: "Hello world."})
    return in_memory_engine


@pytest.fixture
def seed_three_sentences(in_memory_engine):
    _seed_data(in_memory_engine, {0: "First.", 1: "Second.", 2: "Third."})
    return in_memory_engine


@pytest.fixture
def ws_client(seed_three_sentences, mock_kokoro):
    eng = seed_three_sentences
    with _patch_db(eng):
        set_kokoro(mock_kokoro)
        from main import app
        with TestClient(app) as client:
            yield client


@pytest.fixture
def ws_client_single(seed_single_sentence, mock_kokoro):
    eng = seed_single_sentence
    with _patch_db(eng):
        set_kokoro(mock_kokoro)
        from main import app
        with TestClient(app) as client:
            yield client


class TestWebSocketIntegration:
    def test_play_sends_sentence_start_before_binary_chunks(self, ws_client):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json({
                "action": "play",
                "from_index": 0,
                "voice": "af_heart",
                "speed": 1.0,
                "session_id": 42,
            })

            first_msg = ws.receive_text()
            data = json.loads(first_msg)
            assert data["type"] == "sentence_start"
            assert data["index"] == 0
            assert data["session_id"] == 42

    def test_sentence_end_duration_ms_matches_chunk_count_and_speed(self, seed_three_sentences, mock_kokoro):
        eng = seed_three_sentences
        with _patch_db(eng):
            set_kokoro(mock_kokoro)
            from main import app
            with TestClient(app) as client:
                with client.websocket_connect("/ws/tts/test-book") as ws:
                    ws.send_json({"action": "play", "from_index": 0, "voice": "af_heart", "speed": 2.0, "session_id": 99})
                    chunks = 0
                    sentence_end = None
                    while sentence_end is None:
                        try:
                            raw = ws.receive_text()
                            data = json.loads(raw)
                            if data["type"] == "sentence_end":
                                sentence_end = data
                                break
                        except Exception:
                            try:
                                ws.receive_bytes()
                                chunks += 1
                            except Exception:
                                break
                    assert sentence_end is not None
                    assert sentence_end["duration_ms"] == int(chunks * 100 / 2.0)

    def test_seek_to_index_two_first_sentence_start_is_index_two(self, ws_client):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json({
                "action": "seek",
                "to_index": 2,
                "voice": "af_heart",
                "speed": 1.0,
                "session_id": 7,
            })

            first_msg = ws.receive_text()
            data = json.loads(first_msg)
            assert data["type"] == "sentence_start"
            assert data["index"] == 2

    def test_seek_sends_correct_session_id_in_all_messages(self, ws_client):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json({"action": "seek", "to_index": 1, "voice": "af_heart", "speed": 1.0, "session_id": 123})
            for _ in range(50):
                try:
                    raw = ws.receive_text()
                    data = json.loads(raw)
                    assert data["session_id"] == 123
                    if data["type"] == "complete":
                        break
                except Exception:
                    try:
                        ws.receive_bytes()
                    except Exception:
                        break

    def test_complete_after_last_sentence_of_single_sentence_book(self, ws_client_single):
        with ws_client_single.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json({"action": "play", "from_index": 0, "voice": "af_heart", "speed": 1.0, "session_id": 1})
            received_complete = False
            for _ in range(50):
                try:
                    raw = ws.receive_text()
                    data = json.loads(raw)
                    if data["type"] == "complete":
                        assert data["session_id"] == 1
                        received_complete = True
                        break
                except Exception:
                    try:
                        ws.receive_bytes()
                    except Exception:
                        break
            assert received_complete

    def test_filtered_sentence_not_sent(self, mock_kokoro):
        eng = _build_engine()
        with Session(eng) as session:
            book = Book(
                id="test-book",
                title="Test Book",
                author="Test Author",
                file_path="/test/path.pdf",
                file_type="pdf",
                page_count=1,
                cover_page=0,
                created_at=datetime.now(UTC),
            )
            session.add(book)
            session.commit()
            for idx, (text_val, filt) in enumerate([
                ("Before.", False),
                ("Filtered.", True),
                ("After.", False),
            ]):
                sent = Sentence(
                    book_id="test-book",
                    index=idx,
                    text=text_val,
                    page=1,
                    x0=0.0,
                    y0=0.0,
                    x1=100.0,
                    y1=20.0,
                    filtered=filt,
                )
                session.add(sent)
            session.commit()

        with _patch_db(eng):
            set_kokoro(mock_kokoro)
            from main import app
            with TestClient(app) as client:
                with client.websocket_connect("/ws/tts/test-book") as ws:
                    ws.send_json({
                        "action": "play",
                        "from_index": 0,
                        "voice": "af_heart",
                        "speed": 1.0,
                        "session_id": 5,
                    })
                    collected_indices = []
                    for _ in range(30):
                        try:
                            raw = ws.receive_text()
                            data = json.loads(raw)
                            if data["type"] == "sentence_start":
                                collected_indices.append(data["index"])
                            if data["type"] == "complete":
                                break
                        except Exception:
                            try:
                                ws.receive_bytes()
                            except Exception:
                                break
                    assert 1 not in collected_indices

    def test_invalid_book_id_closes_connection(self, ws_client):
        with pytest.raises((WebSocketDisconnect, Exception)):
            with ws_client.websocket_connect("/ws/tts/does-not-exist") as ws:
                ws.receive_text()

    def test_multiple_sentences_arrive_in_order(self, ws_client):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json({
                "action": "play",
                "from_index": 0,
                "voice": "af_heart",
                "speed": 1.0,
                "session_id": 9,
            })
            collected = []
            for _ in range(60):
                try:
                    raw = ws.receive_text()
                    data = json.loads(raw)
                    if data["type"] == "sentence_start":
                        collected.append(data["index"])
                    if data["type"] == "complete":
                        break
                except Exception:
                    try:
                        ws.receive_bytes()
                    except Exception:
                        break
            assert collected == [0, 1, 2]

    def test_pause_stops_stream(self, ws_client):
        import threading
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json({
                "action": "play",
                "from_index": 0,
                "voice": "af_heart",
                "speed": 1.0,
                "session_id": 11,
            })
            ws.send_json({"action": "pause"})
            sentence_start_count = 0
            for _ in range(10):
                result = [None]
                def _read():
                    try:
                        result[0] = ws.receive_text()
                    except Exception:
                        result[0] = None
                t = threading.Thread(target=_read, daemon=True)
                t.start()
                t.join(timeout=3.0)
                if t.is_alive() or result[0] is None:
                    break
                data = json.loads(result[0])
                if data.get("type") == "sentence_start":
                    sentence_start_count += 1
            assert sentence_start_count < 3
