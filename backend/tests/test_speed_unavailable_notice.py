"""The client must be told when the rate it asked for is not the rate it gets.

``TTSEngine`` records ``effective_speed`` in ``_sentence_meta``, but nothing ever
read it — the capability was computed and then discarded. A reader who selected
1.5x while the installed Kokoro could not honour ``speed=`` therefore heard 1.0x
audio with the UI still showing 1.5x: silent, not an error, which is exactly the
pretended capability the handoff's rule 7 forbids.

The notice is per session rather than per sentence because the ability to honour
a rate is a property of the engine, not of a sentence, and repeating it would be
noise. It carries ``session_id`` so the client's existing stale-session filter
discards it after a seek, like every other message on this socket.
"""
import json

import numpy as np


def _unsupported_kokoro(text, voice="af_heart", **kwargs):
    """A pipeline with no ``speed`` parameter, so the engine degrades to 1.0x.

    Accepting ``**kwargs`` but not naming ``speed`` is exactly what
    ``_accepts_speed`` inspects the signature for.
    """
    yield (None, None, np.ones(2400, dtype=np.float32))


def _texts(messages):
    return [m["data"] for m in messages if m["channel"] == "text"]


def _play(speed, *, index=0, session_id=1):
    return {
        "action": "play",
        "from_index": index,
        "voice": "af_heart",
        "speed": speed,
        "session_id": session_id,
    }


class TestSpeedUnavailableNotice:
    def test_a_downgraded_rate_is_reported_once_per_session(
        self, ws_client_factory, ws_read
    ):
        with ws_client_factory(kokoro=_unsupported_kokoro) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_text(json.dumps(_play(1.5)))
                messages = ws_read(ws, until=("complete",))

        notices = [m for m in _texts(messages) if m.get("type") == "speed_unavailable"]
        assert len(notices) == 1, (
            f"expected exactly one notice for the session, got {len(notices)}: "
            f"{[m.get('type') for m in _texts(messages)]}"
        )
        assert notices[0]["requested_speed"] == 1.5
        assert notices[0]["effective_speed"] == 1.0
        assert notices[0]["session_id"] == 1

    def test_no_notice_when_the_rate_is_honoured(
        self, ws_client_factory, ws_read
    ):
        """A build that honours ``speed=`` must stay silent about speed."""
        with ws_client_factory() as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_text(json.dumps(_play(1.5)))
                messages = ws_read(ws, until=("complete",))

        types = [m.get("type") for m in _texts(messages)]
        assert "speed_unavailable" not in types, (
            f"a speed control that works must not produce a notice: {types}"
        )

    def test_the_notice_does_not_replace_normal_stream_messages(
        self, ws_client_factory, ws_read
    ):
        """Reporting the downgrade must not cost the client its audio or events."""
        with ws_client_factory(kokoro=_unsupported_kokoro) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_text(json.dumps(_play(1.5)))
                messages = ws_read(ws, until=("complete",))

        types = [m.get("type") for m in _texts(messages)]
        assert types.count("speed_unavailable") == 1
        assert "sentence_end" in types, f"stream lost its sentence_end: {types}"
        assert "complete" in types, f"stream never completed: {types}"
        assert any(m["channel"] == "bytes" for m in messages), "no audio was streamed"
