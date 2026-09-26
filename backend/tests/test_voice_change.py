"""Tests that the voice parameter flows through the WebSocket to the TTS engine.

Uses the ``ws_client_factory`` fixture from conftest.py, which swaps
``db.database.engine`` for a per-test temp database through ``monkeypatch``. The
previous version of this file wrote a ``NamedTemporaryFile`` per test and never
unlinked it, assigned ``db.database.engine`` without restoring it, and swallowed
read failures with ``except Exception``.
"""
import numpy as np

SENTENCE_SAMPLES = 2400


def _voice_logging_kokoro(voice_log, samples=SENTENCE_SAMPLES):
    """A fake pipeline that records the voice it was asked for."""

    def kokoro(text, voice="af_heart", speed=1.0):
        voice_log.append(voice)
        yield (None, None, np.ones(samples, dtype=np.float32))

    return kokoro


def _play(index=0, *, voice=None, speed=1.0, session_id=1, action="play"):
    payload = {"action": action, "speed": speed, "session_id": session_id}
    payload["from_index" if action == "play" else "to_index"] = index
    if voice is not None:
        payload["voice"] = voice
    return payload


class TestVoiceChange:
    def test_play_passes_voice_to_engine(self, ws_client_factory, ws_read):
        voice_log: list[str] = []

        with ws_client_factory(kokoro=_voice_logging_kokoro(voice_log)) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, voice="bf_emma", session_id=1))
                messages = ws_read(ws, until=("complete",))

        assert any(m["channel"] == "text" and m["data"]["type"] == "complete" for m in messages)
        assert voice_log, "the engine was never asked to synthesize"
        assert set(voice_log) == {"bf_emma"}

    def test_voice_switch_mid_session(self, ws_client_factory, ws_read):
        voice_log: list[str] = []

        with ws_client_factory(kokoro=_voice_logging_kokoro(voice_log)) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, voice="af_heart", session_id=1))
                ws_read(ws, until=("complete",))

                first_voices = list(voice_log)
                voice_log.clear()

                ws.send_json(_play(0, voice="am_adam", session_id=2))
                ws_read(ws, until=("complete",))
                second_voices = list(voice_log)

        assert first_voices and set(first_voices) == {"af_heart"}
        assert second_voices and set(second_voices) == {"am_adam"}

    def test_seek_passes_voice(self, ws_client_factory, ws_read):
        voice_log: list[str] = []

        with ws_client_factory(kokoro=_voice_logging_kokoro(voice_log)) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(2, voice="af_nicole", speed=1.0, session_id=5, action="seek"))
                messages = ws_read(ws, until=("complete",))

        starts = [
            m["data"]["index"]
            for m in messages
            if m["channel"] == "text" and m["data"]["type"] == "sentence_start"
        ]
        assert starts == [2]
        assert voice_log and set(voice_log) == {"af_nicole"}

    def test_default_voice_when_omitted(self, ws_client_factory, ws_read):
        voice_log: list[str] = []

        with ws_client_factory(kokoro=_voice_logging_kokoro(voice_log)) as client:
            with client.websocket_connect("/ws/tts/test-book") as ws:
                ws.send_json(_play(0, session_id=1))
                ws_read(ws, until=("complete",))

        assert voice_log and set(voice_log) == {"af_heart"}
