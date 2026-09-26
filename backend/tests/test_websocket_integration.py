"""End-to-end tests for the TTS WebSocket handler in routers/tts.py.

Everything here drives the real handler through a TestClient, against a real
(per-test, temp-file) SQLite database and a fake Kokoro pipeline -- see the
fixtures in conftest.py: ``ws_client`` (three plain sentences) and
``ws_client_factory`` (any book you want).

Two things the previous version of this file got wrong and this one does not:

* Messages are read with the ``ws_read`` collector, which distinguishes text
  from binary instead of relying on ``except Exception: break`` loops. Those
  loops silently ended the test whenever anything went wrong, so a broken stream
  looked the same as a finished one.
* ``duration_ms`` is asserted against the audio Kokoro actually returned. The
  old assertions recomputed ``int(chunks * 100 / speed)``, which stopped being
  the contract in 9eb454a when the router started reporting the real audio
  length from ``engine._sentence_meta``.
"""
import json
import time

import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

SENTENCE_SAMPLES = 2400  # 100 ms at 24 kHz -- one streamed chunk
SPEEDS_AND_DURATIONS = [(0.5, 200), (1.0, 100), (1.5, 66), (2.0, 50)]


def _play(index=0, *, voice="af_heart", speed=1.0, session_id=1, action="play"):
    key = "from_index" if action == "play" else "to_index"
    return {"action": action, key: index, "voice": voice, "speed": speed, "session_id": session_id}


def _texts(messages):
    return [m["data"] for m in messages if m["channel"] == "text"]


def _chunks(messages):
    return [m["data"] for m in messages if m["channel"] == "bytes"]


def _types(messages):
    return [m["type"] for m in _texts(messages)]


def _speed_aware_kokoro(samples_per_result=SENTENCE_SAMPLES):
    """Fake pipeline whose audio length follows the speed, as the real one does."""

    def kokoro(text, voice="af_heart", speed=1.0):
        count = int(round(samples_per_result / (float(speed) or 1.0)))
        yield (None, None, np.ones(max(1, count), dtype=np.float32))

    return kokoro


def _slow_kokoro(results=6, delay=0.05, samples=SENTENCE_SAMPLES):
    """Fake pipeline that takes `results * delay` seconds per sentence."""

    def kokoro(text, voice="af_heart", speed=1.0):
        for _ in range(results):
            time.sleep(delay)
            yield (None, None, np.ones(samples, dtype=np.float32))

    return kokoro


class TestPlaybackProtocol:
    def test_play_sends_sentence_start_before_any_audio(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(0, session_id=42))
            messages = ws_read(ws, until=("complete",))

        assert messages[0]["channel"] == "text"
        start = messages[0]["data"]
        assert start == {"type": "sentence_start", "index": 0, "session_id": 42}
        assert messages[1]["channel"] == "bytes", "audio must follow its sentence_start"

    def test_every_audio_chunk_is_a_wav(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(0, session_id=1))
            messages = ws_read(ws, until=("complete",))

        chunks = _chunks(messages)
        assert len(chunks) == 3, "one 100 ms chunk per sentence"
        assert all(chunk.startswith(b"RIFF") for chunk in chunks)

    @pytest.mark.parametrize("speed,expected_ms", SPEEDS_AND_DURATIONS)
    def test_sentence_end_duration_is_the_real_audio_length(
        self, ws_client_factory, monkeypatch, ws_read, speed, expected_ms
    ):
        """The reported duration is the audio Kokoro returned, so it shrinks as
        the speed rises (2400/speed samples at 24 kHz)."""
        import routers.tts as tts_router

        with ws_client_factory(sentences=[{"index": 0, "text": "Hello."}]) as client:
            monkeypatch.setattr(tts_router, "_kokoro", _speed_aware_kokoro())
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, speed=speed, session_id=1))
                messages = ws_read(ws, until=("complete",))

        ends = [m for m in _texts(messages) if m["type"] == "sentence_end"]
        assert len(ends) == 1
        assert ends[0]["duration_ms"] == expected_ms

    def test_slower_speed_reports_a_longer_duration(self, ws_client_factory, monkeypatch, ws_read):
        import routers.tts as tts_router

        durations = {}
        with ws_client_factory(sentences=[{"index": 0, "text": "Hello."}]) as client:
            monkeypatch.setattr(tts_router, "_kokoro", _speed_aware_kokoro())
            for speed in (0.5, 1.0):
                with client.websocket_connect("/ws/tts/test-book") as ws:
                    ws.send_json(_play(0, speed=speed, session_id=int(speed * 10)))
                    ends = [
                        m
                        for m in _texts(ws_read(ws, until=("complete",)))
                        if m["type"] == "sentence_end"
                    ]
                durations[speed] = ends[0]["duration_ms"]

        assert durations[0.5] == 2 * durations[1.0]

    def test_sentences_arrive_in_order_and_end_with_complete(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(0, session_id=9))
            messages = ws_read(ws, until=("complete",))

        starts = [m["index"] for m in _texts(messages) if m["type"] == "sentence_start"]
        assert starts == [0, 1, 2]
        assert _types(messages)[-1] == "complete"

    def test_each_sentence_is_started_then_ended(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(0, session_id=7))
            messages = ws_read(ws, until=("complete",))

        text_types = _types(messages)
        assert text_types[0] == "sentence_start"
        assert "sentence_end" in text_types
        assert text_types[-1] == "complete"
        for index in (0, 1, 2):
            assert text_types.index("sentence_start") < text_types.index("sentence_end")

    def test_from_index_beyond_sentences_sends_complete_only(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(99, session_id=4))
            messages = ws_read(ws, until=("complete",))

        assert _types(messages) == ["complete"]
        assert _texts(messages)[0]["session_id"] == 4

    def test_seek_starts_at_the_requested_sentence(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(2, session_id=7, action="seek"))
            messages = ws_read(ws, until=("complete",))

        starts = [m["index"] for m in _texts(messages) if m["type"] == "sentence_start"]
        assert starts == [2]

    def test_seek_tags_every_message_with_the_session_id(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(1, session_id=123, action="seek"))
            messages = ws_read(ws, until=("complete",))

        text = _texts(messages)
        assert text, "the seek produced no text messages"
        assert {m["session_id"] for m in text} == {123}
        assert text[-1]["type"] == "complete"


class TestBookSelection:
    def test_single_sentence_book_completes_after_it(self, ws_client_factory, ws_read):
        with ws_client_factory(sentences=[{"index": 0, "text": "Only."}]) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, session_id=1))
                messages = ws_read(ws, until=("complete",))

        text = _texts(messages)
        assert [m["type"] for m in text] == ["sentence_start", "sentence_end", "complete"]
        assert all(m["session_id"] == 1 for m in text)

    def test_filtered_sentences_are_never_sent(self, ws_client_factory, ws_read):
        sentences = [
            {"index": 0, "text": "Before.", "filtered": False},
            {"index": 1, "text": "Filtered.", "filtered": True},
            {"index": 2, "text": "After.", "filtered": False},
        ]
        with ws_client_factory(sentences=sentences) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, session_id=5))
                messages = ws_read(ws, until=("complete",))

        starts = [m["index"] for m in _texts(messages) if m["type"] == "sentence_start"]
        assert starts == [0, 2], "index 1 is filtered and must be skipped entirely"

    def test_unknown_book_closes_the_socket_with_4004(self, ws_client):
        with pytest.raises(WebSocketDisconnect) as raised:
            with ws_client.websocket_connect("/ws/tts/does-not-exist") as ws:
                ws.receive_text()

        assert raised.value.code == 4004


class TestSessionHandover:
    def test_new_play_supersedes_the_previous_session(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(0, session_id=1))
            ws.send_json(_play(0, session_id=2))
            messages = ws_read(ws, until=("complete",))

        text = _texts(messages)
        assert any(m["session_id"] == 2 for m in text), "session 2 produced nothing"
        first_new = next(i for i, m in enumerate(text) if m["session_id"] == 2)
        assert {m["session_id"] for m in text[first_new:]} == {2}
        assert text[-1] == {"type": "complete", "session_id": 2}

    def test_pause_stops_the_stream_without_completing_it(
        self, ws_client_factory, monkeypatch, ws_read
    ):
        """A pause must stop playback mid-book and must not look like the end of
        the book to the client, which relies on ``complete``."""
        import routers.tts as tts_router

        sentences = [{"index": i, "text": f"Sentence {i}."} for i in range(20)]
        with ws_client_factory(sentences=sentences) as client:
            # ~0.3 s per sentence, so the pause reliably lands mid-stream and a
            # stream that kept running would reveal itself within the drain.
            monkeypatch.setattr(tts_router, "_kokoro", _slow_kokoro(results=6, delay=0.05))
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, session_id=11))
                started = ws_read(ws, until=("sentence_start",))
                assert [m["type"] for m in _texts(started)] == ["sentence_start"]

                ws.send_json({"action": "pause"})
                after_pause = ws_read(ws, expect_silence=True, quiet=1.5)

        text = _texts(after_pause)
        assert "complete" not in [m["type"] for m in text], (
            "a pause must not be reported as the end of the book"
        )
        started_after = [m["index"] for m in text if m["type"] == "sentence_start"]
        assert all(index <= 1 for index in started_after), (
            f"pause did not stop the stream, it went on to sentence(s) {started_after}"
        )

    def test_stream_continues_when_not_paused(self, ws_client_factory, monkeypatch, ws_read):
        """Negative control for the pause test above.

        Identical setup and identical drain window, but nothing interrupts the
        stream: it must run on well past sentence 1. Without this, the pause
        test's ``index <= 1`` bound could hold simply because the fake had
        stopped producing.
        """
        import routers.tts as tts_router

        sentences = [{"index": i, "text": f"Sentence {i}."} for i in range(20)]
        with ws_client_factory(sentences=sentences) as client:
            monkeypatch.setattr(tts_router, "_kokoro", _slow_kokoro(results=6, delay=0.05))
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, session_id=11))
                ws_read(ws, until=("sentence_start",))
                rest = ws_read(ws, quiet=1.5)

        text = _texts(rest)
        starts = [m["index"] for m in text if m["type"] == "sentence_start"]
        assert starts and max(starts) >= 2, f"an unpaused stream only reached {starts}"
        assert _chunks(rest), "no audio was streamed"

    def test_pause_then_play_starts_a_fresh_session(self, ws_client, ws_read):
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json({"action": "pause"})
            ws.send_json(_play(1, session_id=77))
            messages = ws_read(ws, until=("complete",))

        starts = [m["index"] for m in _texts(messages) if m["type"] == "sentence_start"]
        assert starts == [1, 2]
        assert {m["session_id"] for m in _texts(messages)} == {77}

    def test_client_disconnect_mid_stream_leaves_the_server_healthy(self, ws_client, ws_read):
        """_consumer_with_events must exit cleanly when the client goes away."""
        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json(_play(0, session_id=1))
            assert ws_read(ws, until=("sentence_start",), quiet=0.5)[0]["data"]["type"] == (
                "sentence_start"
            )
            # Leaving the block closes the socket mid-stream.

        with ws_client.websocket_connect("/ws/tts/test-book") as ws:
            ws.send_json({"action": "pause"})


class TestSpeedForwarding:
    """The WS play payload must forward speed to KPipeline at every speed."""

    @pytest.mark.parametrize("speed", [1.0, 1.5, 2.0, 3.0])
    def test_websocket_play_forwards_speed_to_kokoro(
        self, ws_client_factory, monkeypatch, ws_read, speed
    ):
        import routers.tts as tts_router

        seen: list[float] = []

        def spy_kokoro(text, voice="af_heart", speed=1.0):
            seen.append(speed)
            yield (None, None, np.ones(SENTENCE_SAMPLES, dtype=np.float32))

        with ws_client_factory(sentences=[{"index": 0, "text": "First."}]) as client:
            # The handler reads the module global when the socket connects.
            monkeypatch.setattr(tts_router, "_kokoro", spy_kokoro)
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, speed=speed, session_id=1))
                messages = ws_read(ws, until=("complete",))

        assert _texts(messages)[0]["type"] == "sentence_start"
        assert seen, "the handler never called the pipeline"
        assert set(seen) == {speed}, f"expected speed={speed} at KPipeline, got {seen}"
