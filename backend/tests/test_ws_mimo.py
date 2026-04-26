import json
from datetime import datetime, UTC

import numpy as np
import pytest
from fastapi import FastAPI
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine
from starlette.testclient import TestClient

import db.database as _db
from db.models import Book, Sentence
from routers import tts as tts_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine():
    """In-memory SQLite engine that shares a single connection across all
    sessions (StaticPool).  Without this, each Session opens a fresh connection
    to ``sqlite:///:memory:`` and gets a *different* empty database."""
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _make_app():
    """Build a minimal FastAPI app wired to the TTS router."""
    app = FastAPI()
    app.include_router(tts_router.router)
    return app


def _make_kokoro(samples_per_result: int = 2400):
    """Return a fake Kokoro callable that yields 3-tuples like the real one.

    Each invocation returns a single (graphemes, phonemes, ndarray) where the
    ndarray has *samples_per_result* samples.  With sample_rate=24000 and
    chunk = sample_rate // 10 = 2400, this produces
    ``ceil(samples_per_result / 2400)`` binary WAV chunks.
    """
    def kokoro(text, voice="af_heart", speed=1.0):
        return [(None, None, np.ones(samples_per_result, dtype=np.float32))]
    return kokoro


def _seed(engine, book_id="book-1", sentences=None):
    """Insert a Book + Sentence rows."""
    if sentences is None:
        sentences = [
            {"index": 0, "text": "Hello world.", "page": 1,
             "x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0, "filtered": False},
            {"index": 1, "text": "Second sentence.", "page": 1,
             "x0": 0.0, "y0": 2.0, "x1": 1.0, "y1": 3.0, "filtered": False},
            {"index": 2, "text": "Third sentence.", "page": 1,
             "x0": 0.0, "y0": 4.0, "x1": 1.0, "y1": 5.0, "filtered": False},
        ]
    with Session(engine) as session:
        session.add(Book(
            id=book_id,
            title="Test Book",
            file_path="/tmp/test.pdf",
            file_type="pdf",
            page_count=1,
            created_at=datetime.now(UTC),
        ))
        for s in sentences:
            session.add(Sentence(book_id=book_id, **s))
        session.commit()


@pytest.fixture()
def ws_app():
    """Create an in-memory engine, seed data, inject mock, return TestClient."""
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)

    # Patch engine in both modules that use it at call-time
    tts_router._db.engine = engine
    import services.tts_engine as _tts_mod
    _tts_mod._db.engine = engine

    tts_router.set_kokoro(_make_kokoro())
    _seed(engine)

    app = _make_app()
    client = TestClient(app)
    yield client
    client.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_play_returns_sentence_start_then_chunks_then_end(ws_app):
    """Ordering: sentence_start → binary chunks → sentence_end for each sentence."""
    with ws_app.websocket_connect("/ws/tts/book-1") as ws:
        ws.send_json({"action": "play", "from_index": 0,
                       "voice": "af_heart", "speed": 1.0, "session_id": 1})

        for expected_index in range(3):
            msg = ws.receive_text()
            data = json.loads(msg)
            assert data["type"] == "sentence_start"
            assert data["index"] == expected_index

            # One binary chunk (2400 samples = 1 chunk)
            chunk = ws.receive_bytes()
            assert isinstance(chunk, bytes) and len(chunk) > 0

            msg = ws.receive_text()
            data = json.loads(msg)
            assert data["type"] == "sentence_end"
            assert data["index"] == expected_index

        # trailing complete
        msg = ws.receive_text()
        assert json.loads(msg)["type"] == "complete"


def test_duration_ms_formula_speed_15(ws_app):
    """speed=1.5, check duration_ms == int(chunk_count * 100 / 1.5)."""
    # 7200 samples → 3 chunks (ceil(7200/2400))
    tts_router.set_kokoro(_make_kokoro(samples_per_result=7200))

    with ws_app.websocket_connect("/ws/tts/book-1") as ws:
        ws.send_json({"action": "play", "from_index": 0,
                       "voice": "af_heart", "speed": 1.5, "session_id": 2})

        # Consume sentence_start + chunk for first sentence
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "sentence_start"
        ws.receive_bytes()  # chunk 1
        ws.receive_bytes()  # chunk 2
        ws.receive_bytes()  # chunk 3

        msg = json.loads(ws.receive_text())
        assert msg["type"] == "sentence_end"
        chunk_count = 3
        expected = int(chunk_count * 100 / 1.5)
        assert msg["duration_ms"] == expected


def test_seek_starts_from_correct_sentence(ws_app):
    """Seek to index 1 — first sentence_start.index must be 1."""
    with ws_app.websocket_connect("/ws/tts/book-1") as ws:
        ws.send_json({"action": "seek", "to_index": 1,
                       "voice": "af_heart", "speed": 1.0, "session_id": 3})

        msg = json.loads(ws.receive_text())
        assert msg["type"] == "sentence_start"
        assert msg["index"] == 1


def test_session_id_matches_in_all_messages(ws_app):
    """All text messages carry session_id == 99."""
    with ws_app.websocket_connect("/ws/tts/book-1") as ws:
        ws.send_json({"action": "play", "from_index": 0,
                       "voice": "af_heart", "speed": 1.0, "session_id": 99})

        # Drain all messages until complete
        while True:
            raw = ws.receive_text()
            data = json.loads(raw)
            assert data["session_id"] == 99
            if data["type"] == "complete":
                break
            # binary chunk(s) between sentence_start and sentence_end
            while True:
                try:
                    ws.receive_bytes()
                except Exception:
                    break


def test_complete_message_received(ws_app):
    """Playing to the end of the book yields a {type:'complete'} message."""
    with ws_app.websocket_connect("/ws/tts/book-1") as ws:
        ws.send_json({"action": "play", "from_index": 0,
                       "voice": "af_heart", "speed": 1.0, "session_id": 4})

        got_complete = False
        for _ in range(100):  # safety bound
            raw = ws.receive_text()
            data = json.loads(raw)
            if data["type"] == "complete":
                got_complete = True
                break
            # skip binary chunk(s)
            while True:
                try:
                    ws.receive_bytes()
                except Exception:
                    break
        assert got_complete, "Never received complete message"


def test_play_after_seek_uses_new_session(ws_app):
    """Seek with session_id=5, then play with session_id=6.
    Once session 6 messages start arriving, no session_id=5 may appear."""
    with ws_app.websocket_connect("/ws/tts/book-1") as ws:
        # First: seek with session 5
        ws.send_json({"action": "seek", "to_index": 0,
                       "voice": "af_heart", "speed": 1.0, "session_id": 5})

        # Immediately send play with session 6 (this cancels session 5)
        ws.send_json({"action": "play", "from_index": 0,
                       "voice": "af_heart", "speed": 1.0, "session_id": 6})

        # Drain messages until we see session 6, then verify no session 5 leaks
        seen_session_6 = False
        for _ in range(200):
            msg = ws.receive()
            # Binary messages from interrupted seek — skip
            if "bytes" in msg:
                continue
            data = json.loads(msg["text"])

            if data["session_id"] == 6:
                seen_session_6 = True

            if seen_session_6:
                assert data["session_id"] != 5, (
                    f"Got stale session_id=5 after session 6 started: {data}"
                )

            if data["type"] == "complete":
                break

        assert seen_session_6, "Never received any session_id=6 message"


def test_filtered_sentences_not_sent(ws_app):
    """A filtered sentence (index 1) must not appear as sentence_start."""
    # Insert an extra book with one filtered sentence
    engine = tts_router._db.engine
    with Session(engine) as session:
        session.add(Book(
            id="book-filtered",
            title="Filtered Book",
            file_path="/tmp/filtered.pdf",
            file_type="pdf",
            page_count=1,
            created_at=datetime.now(UTC),
        ))
        session.add(Sentence(
            book_id="book-filtered", index=0, text="Visible.", page=1,
            x0=0.0, y0=0.0, x1=1.0, y1=1.0, filtered=False,
        ))
        session.add(Sentence(
            book_id="book-filtered", index=1, text="Filtered out.", page=1,
            x0=0.0, y0=2.0, x1=1.0, y1=3.0, filtered=True,
        ))
        session.add(Sentence(
            book_id="book-filtered", index=2, text="Also visible.", page=1,
            x0=0.0, y0=4.0, x1=1.0, y1=5.0, filtered=False,
        ))
        session.commit()

    with ws_app.websocket_connect("/ws/tts/book-filtered") as ws:
        ws.send_json({"action": "play", "from_index": 0,
                       "voice": "af_heart", "speed": 1.0, "session_id": 7})

        seen_indices = []
        for _ in range(100):
            raw = ws.receive_text()
            data = json.loads(raw)
            if data["type"] == "sentence_start":
                seen_indices.append(data["index"])
            if data["type"] == "complete":
                break
            # drain binary
            while True:
                try:
                    ws.receive_bytes()
                except Exception:
                    break

        assert 1 not in seen_indices, f"Filtered index 1 appeared in {seen_indices}"
        assert seen_indices == [0, 2]
