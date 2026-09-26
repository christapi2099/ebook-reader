"""Offline tests for the Modal remote Kokoro client.

Nothing here touches the network or needs Modal credentials: transports are
injected or faked, so the suite passes on a machine with no network at all.
"""
import base64
import sys
import time
import types

import numpy as np
import pytest

from services import modal_remote
from services.modal_remote import (
    KokoroChunk,
    KokoroToken,
    ModalKokoroClient,
    RemoteConfig,
    RemoteSynthesisError,
    decode_response,
    modal_credentials_present,
    probe,
    reset_probe_cache,
    resolve_transport,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _audio(samples=2400) -> np.ndarray:
    return (np.arange(samples, dtype=np.float32) / samples).astype(np.float32)


def _wire_chunk(audio=None, graphemes="Hello world.", phonemes="həˈloʊ wɜːld", tokens=()) -> dict:
    audio = _audio() if audio is None else audio
    return {
        "graphemes": graphemes,
        "phonemes": phonemes,
        "audio_b64": base64.b64encode(np.asarray(audio, dtype="<f4").tobytes()).decode("ascii"),
        "samples": int(np.asarray(audio).size),
        "tokens": list(tokens),
    }


def _wire(audio=None, tokens=()) -> dict:
    return {"sample_rate": 24000, "device": "cuda", "chunks": [_wire_chunk(audio, tokens=tokens)]}


def _env(**overrides) -> dict:
    """Environment mapping that can never fall back to the developer's ~/.modal.toml."""
    env = {"MODAL_CONFIG_PATH": "/nonexistent/modal.toml"}
    env.update(overrides)
    return env


def _client(invoke, **config_kwargs) -> ModalKokoroClient:
    config = RemoteConfig(**{"transport": "sdk", **config_kwargs})
    return ModalKokoroClient(config=config, invoke=invoke)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    reset_probe_cache()
    yield
    reset_probe_cache()


# --------------------------------------------------------------------------
# The drop-in shape
# --------------------------------------------------------------------------


class TestChunkShape:
    def test_last_index_is_the_audio(self):
        audio = _audio()
        chunk = KokoroChunk("g", "p", audio)
        assert chunk[-1] is audio

    def test_unpacks_as_a_three_tuple(self):
        audio = _audio()
        graphemes, phonemes, unpacked = KokoroChunk("g", "p", audio)
        assert (graphemes, phonemes) == ("g", "p")
        assert unpacked is audio

    def test_len_is_three_and_indexing_is_ordered(self):
        chunk = KokoroChunk("g", "p", _audio())
        assert len(chunk) == 3
        assert (chunk[0], chunk[1]) == ("g", "p")

    def test_engine_style_attribute_access(self):
        chunk = KokoroChunk("g", "p", _audio(), tokens=[KokoroToken("hi", "haɪ", 0.0, 0.5)])
        assert chunk.tokens[0].text == "hi"


class TestClientCall:
    def test_returns_decoded_float32_audio(self):
        client = _client(lambda payload: _wire())
        chunks = client("Hello world.", voice="af_heart", speed=1.0)
        assert len(chunks) == 1
        assert isinstance(chunks[0].audio, np.ndarray)
        assert chunks[0].audio.dtype == np.float32
        assert chunks[0].audio.size == 2400
        assert chunks[0].graphemes == "Hello world."

    def test_sends_voice_speed_and_lang_code(self):
        seen = {}

        def invoke(payload):
            seen.update(payload)
            return _wire()

        client = _client(invoke, lang_code="b")
        client("Text.", voice="am_michael", speed=1.5)
        assert seen == {"text": "Text.", "voice": "am_michael", "speed": 1.5, "lang_code": "b"}

    def test_blank_text_never_calls_the_backend(self):
        def invoke(payload):  # pragma: no cover - must not run
            raise AssertionError("blank text must not hit the network")

        assert _client(invoke)("   ") == []

    def test_injected_invoke_is_reported_as_injected(self):
        assert _client(lambda payload: _wire()).transport == "injected"


class TestErrorHandling:
    def test_transport_failure_is_wrapped(self):
        def invoke(payload):
            raise RuntimeError("connection reset")

        with pytest.raises(RemoteSynthesisError, match="connection reset"):
            _client(invoke)("Hello.")

    def test_slow_call_raises_a_cold_start_timeout(self):
        def invoke(payload):
            time.sleep(0.3)
            return _wire()

        client = _client(invoke, timeout_s=0.05)
        with pytest.raises(RemoteSynthesisError, match="did not answer within"):
            client("Hello.")

    def test_missing_credentials_raise_clearly(self, monkeypatch):
        monkeypatch.setattr(modal_remote, "modal_credentials_present", lambda env=None: False)
        client = ModalKokoroClient(config=RemoteConfig(transport="sdk"))
        with pytest.raises(RemoteSynthesisError, match="No Modal credentials"):
            client("Hello.")

    def test_unconfigured_client_says_so(self, monkeypatch):
        monkeypatch.setattr(modal_remote, "resolve_transport", lambda config, env=None: None)
        client = ModalKokoroClient(config=RemoteConfig(transport="auto"), invoke=None)
        assert client.transport is None
        with pytest.raises(RemoteSynthesisError, match="not configured"):
            client("Hello.")


class TestDecodeResponse:
    def test_wrong_sample_rate_is_rejected(self):
        client = _client(lambda payload: {})
        with pytest.raises(RemoteSynthesisError, match="sample_rate"):
            decode_response({"sample_rate": 16000, "chunks": [_wire_chunk()]}, client.config)

    def test_missing_chunks_is_rejected(self):
        client = _client(lambda payload: {})
        with pytest.raises(RemoteSynthesisError, match="no audio chunks"):
            decode_response({"sample_rate": 24000, "chunks": []}, client.config)

    def test_missing_audio_is_rejected(self):
        client = _client(lambda payload: {})
        bad = {"sample_rate": 24000, "chunks": [{"graphemes": "g"}]}
        with pytest.raises(RemoteSynthesisError, match="missing audio_b64"):
            decode_response(bad, client.config)

    def test_empty_audio_is_rejected(self):
        client = _client(lambda payload: {})
        with pytest.raises(RemoteSynthesisError, match="missing audio_b64"):
            decode_response(_wire(audio=np.zeros(0, dtype=np.float32)), client.config)

    def test_garbage_audio_is_rejected(self):
        client = _client(lambda payload: {})
        bad = {"sample_rate": 24000, "chunks": [{"audio_b64": "!!!!"}]}
        with pytest.raises(RemoteSynthesisError, match="zero samples"):
            decode_response(bad, client.config)

    def test_non_dict_payload_is_rejected(self):
        client = _client(lambda payload: {})
        with pytest.raises(RemoteSynthesisError, match="expected a dict"):
            decode_response(["nope"], client.config)

    def test_undecodable_audio_is_rejected(self):
        client = _client(lambda payload: {})
        bad = {"sample_rate": 24000, "chunks": [{"audio_b64": "a"}]}
        with pytest.raises(RemoteSynthesisError, match="float32"):
            decode_response(bad, client.config)

    def test_tokens_are_rehydrated_into_objects(self):
        client = _client(lambda payload: {})
        wire = _wire(tokens=[{"text": "Hello", "phonemes": "həˈloʊ", "start_ts": 0.1, "end_ts": 0.6}])
        chunk = decode_response(wire, client.config)[0]
        assert isinstance(chunk.tokens[0], KokoroToken)
        assert chunk.tokens[0].text == "Hello"
        assert chunk.tokens[0].end_ts == 0.6


class TestTtsEngineCompatibility:
    """The whole point: ``services/tts_engine.py`` consumes this unchanged."""

    def _engine(self, invoke):
        from services.tts_engine import TTSEngine

        return TTSEngine(_client(invoke))

    def test_call_kokoro_accepts_the_client(self):
        engine = self._engine(lambda payload: _wire())
        results, effective_speed = engine._call_kokoro("Hello.", "af_heart", 1.25)
        assert effective_speed == 1.25
        assert len(results) == 1

    def test_collect_result_reads_audio_and_tokens(self):
        tokens = [
            {"text": "Hello", "phonemes": "həˈloʊ", "start_ts": 0.0, "end_ts": 0.5},
            {"text": ",", "phonemes": "", "start_ts": 0.5, "end_ts": 0.5},
        ]
        engine = self._engine(lambda payload: _wire(tokens=tokens))
        results, _ = engine._call_kokoro("Hello,", "af_heart", 1.0)

        audio_parts: list[np.ndarray] = []
        word_timestamps: list[dict] = []
        offset = 0.0
        for result in results:
            audio, offset = engine._collect_result(result, audio_parts, word_timestamps, offset)

        assert audio.dtype == np.float32
        assert np.allclose(np.concatenate(audio_parts), _audio())
        # Punctuation-only tokens are dropped, exactly as on the local path.
        assert [t["word"] for t in word_timestamps] == ["Hello"]
        assert offset == pytest.approx(0.1, abs=1e-6)  # 2400 samples @ 24 kHz

    def test_engine_streams_expected_duration_from_remote_audio(self):
        engine = self._engine(lambda payload: _wire(audio=np.zeros(24000, dtype=np.float32)))
        results, _ = engine._call_kokoro("Hello.", "af_heart", 1.0)
        parts: list[np.ndarray] = []
        for result in results:
            engine._collect_result(result, parts, [], 0.0)
        assert engine._audio_duration_ms(parts) == 1000


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------


class TestSdkTransport:
    def _install_fake_modal(self, monkeypatch, function):
        module = types.ModuleType("modal")
        module.Function = types.SimpleNamespace(from_name=lambda app, name: function)
        monkeypatch.setitem(sys.modules, "modal", module)
        monkeypatch.setattr(modal_remote, "modal_credentials_present", lambda env=None: True)

    def test_remote_is_called_and_result_returned(self, monkeypatch):
        calls = []
        function = types.SimpleNamespace(remote=lambda payload: calls.append(payload) or _wire())
        self._install_fake_modal(monkeypatch, function)

        client = ModalKokoroClient(config=RemoteConfig(transport="sdk"))
        chunks = client("Hello.")
        assert len(chunks) == 1
        assert calls[0]["text"] == "Hello."

    def test_function_handle_is_cached_between_calls(self, monkeypatch):
        lookups = []
        function = types.SimpleNamespace(remote=lambda payload: _wire())

        def from_name(app, name):
            lookups.append((app, name))
            return function

        module = types.ModuleType("modal")
        module.Function = types.SimpleNamespace(from_name=from_name)
        monkeypatch.setitem(sys.modules, "modal", module)
        monkeypatch.setattr(modal_remote, "modal_credentials_present", lambda env=None: True)

        client = ModalKokoroClient(config=RemoteConfig(transport="sdk"))
        client("One.")
        client("Two.")
        assert len(lookups) == 1

    def test_missing_function_is_reported_as_not_deployed(self, monkeypatch):
        not_found = type("NotFoundError", (Exception,), {})
        function = types.SimpleNamespace(
            remote=lambda payload: (_ for _ in ()).throw(RuntimeError("unused"))
        )
        module = types.ModuleType("modal")

        def from_name(app, name):
            raise not_found("no such function")

        module.Function = types.SimpleNamespace(from_name=from_name)
        monkeypatch.setitem(sys.modules, "modal", module)
        monkeypatch.setattr(modal_remote, "modal_credentials_present", lambda env=None: True)

        client = ModalKokoroClient(config=RemoteConfig(transport="sdk"))
        with pytest.raises(RemoteSynthesisError, match="has `modal deploy` been run"):
            client("Hello.")

    def test_auth_error_is_explained(self, monkeypatch):
        auth_error = type("AuthError", (Exception,), {})
        function = types.SimpleNamespace(remote=lambda payload: _wire())
        self._install_fake_modal(monkeypatch, function)
        monkeypatch.setattr(
            function, "remote", lambda payload: (_ for _ in ()).throw(auth_error("nope"))
        )
        client = ModalKokoroClient(config=RemoteConfig(transport="sdk"))
        with pytest.raises(RemoteSynthesisError, match="authentication failed"):
            client("Hello.")

    def test_failed_call_drops_the_cached_handle(self, monkeypatch):
        lookups = []
        attempts = {"n": 0}

        def remote(payload):
            attempts["n"] += 1
            raise RuntimeError("container died")

        function = types.SimpleNamespace(remote=remote)

        def from_name(app, name):
            lookups.append(name)
            return function

        module = types.ModuleType("modal")
        module.Function = types.SimpleNamespace(from_name=from_name)
        monkeypatch.setitem(sys.modules, "modal", module)
        monkeypatch.setattr(modal_remote, "modal_credentials_present", lambda env=None: True)

        client = ModalKokoroClient(config=RemoteConfig(transport="sdk"))
        for _ in range(2):
            with pytest.raises(RemoteSynthesisError):
                client("Hello.")
        assert len(lookups) == 2  # re-resolved rather than reusing a dead handle


class TestHttpTransport:
    class _FakeHTTPError(Exception):
        pass

    def _install_fake_httpx(self, monkeypatch, response=None, raises=None):
        error_cls = self._FakeHTTPError

        def post(url, json=None, timeout=None):
            if raises is not None:
                raise raises
            return response

        module = types.ModuleType("httpx")
        module.HTTPError = error_cls
        module.post = post
        monkeypatch.setitem(sys.modules, "httpx", module)

    def _response(self, status_code=200, payload=None, text=""):
        return types.SimpleNamespace(
            status_code=status_code, text=text, json=lambda: payload
        )

    def test_successful_post_returns_payload(self, monkeypatch):
        self._install_fake_httpx(monkeypatch, response=self._response(payload=_wire()))
        client = ModalKokoroClient(
            config=RemoteConfig(transport="http"), invoke=modal_remote._http_invoke_factory(
                RemoteConfig(transport="http"), _env(MODAL_KOKORO_URL="https://example.modal.run")
            )
        )
        assert len(client("Hello.")) == 1

    def test_non_200_is_reported_with_status_and_body(self, monkeypatch):
        self._install_fake_httpx(
            monkeypatch, response=self._response(status_code=503, text="container unavailable")
        )
        invoke = modal_remote._http_invoke_factory(
            RemoteConfig(transport="http"), _env(MODAL_KOKORO_URL="https://example.modal.run")
        )
        with pytest.raises(RemoteSynthesisError, match="HTTP 503"):
            invoke({"text": "Hello."})

    def test_network_error_is_reported(self, monkeypatch):
        self._install_fake_httpx(monkeypatch, raises=self._FakeHTTPError("dns failure"))
        invoke = modal_remote._http_invoke_factory(
            RemoteConfig(transport="http"), _env(MODAL_KOKORO_URL="https://example.modal.run")
        )
        with pytest.raises(RemoteSynthesisError, match="Network error"):
            invoke({"text": "Hello."})

    def test_non_json_body_is_reported(self, monkeypatch):
        self._install_fake_httpx(
            monkeypatch,
            response=types.SimpleNamespace(status_code=200, text="<html>", json=lambda: _raise_value_error()),
        )
        invoke = modal_remote._http_invoke_factory(
            RemoteConfig(transport="http"), _env(MODAL_KOKORO_URL="https://example.modal.run")
        )
        with pytest.raises(RemoteSynthesisError, match="non-JSON"):
            invoke({"text": "Hello."})

    def test_missing_url_is_reported(self):
        invoke = modal_remote._http_invoke_factory(RemoteConfig(transport="http"), _env())
        with pytest.raises(RemoteSynthesisError, match="requires MODAL_KOKORO_URL"):
            invoke({"text": "Hello."})


def _raise_value_error():
    raise ValueError("not json")


# --------------------------------------------------------------------------
# Configuration and credentials
# --------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self):
        config = RemoteConfig.from_env(_env())
        assert config.app_name == "kokoro-tts"
        assert config.function_name == "synthesize"
        assert config.transport == "auto"
        assert config.gpu == "T4"

    def test_env_overrides(self):
        config = RemoteConfig.from_env(
            _env(
                MODAL_KOKORO_APP_NAME="my-app",
                MODAL_KOKORO_FUNCTION_NAME="speak",
                MODAL_KOKORO_TRANSPORT="http",
                MODAL_KOKORO_GPU="L4",
                MODAL_KOKORO_TIMEOUT_SECONDS="42",
                MODAL_KOKORO_HEALTH_URL="https://x.modal.run/health",
            )
        )
        assert (config.app_name, config.function_name, config.transport) == ("my-app", "speak", "http")
        assert (config.gpu, config.timeout_s, config.health_url) == ("L4", 42.0, "https://x.modal.run/health")

    def test_unknown_transport_falls_back_to_auto(self):
        assert RemoteConfig.from_env(_env(MODAL_KOKORO_TRANSPORT="carrier-pigeon")).transport == "auto"

    def test_bad_timeout_falls_back_to_default(self):
        config = RemoteConfig.from_env(_env(MODAL_KOKORO_TIMEOUT_SECONDS="soon"))
        assert config.timeout_s == modal_remote.DEFAULT_TIMEOUT_SECONDS

    def test_describe_never_exposes_credentials(self):
        described = RemoteConfig.from_env(_env(MODAL_TOKEN_ID="abc", MODAL_TOKEN_SECRET="def")).describe()
        assert "MODAL_TOKEN_ID" not in described
        assert not any("secret" in key.lower() for key in described)


class TestCredentials:
    def test_env_tokens_count(self):
        assert modal_credentials_present(_env(MODAL_TOKEN_ID="id", MODAL_TOKEN_SECRET="secret")) is True

    def test_partial_env_tokens_do_not_count(self):
        assert modal_credentials_present(_env(MODAL_TOKEN_ID="id")) is False

    def test_modal_toml_profile_counts(self, tmp_path):
        config_path = tmp_path / "modal.toml"
        config_path.write_text('active = true\n\n[christapi2099]\ntoken_id = "id"\ntoken_secret = "secret"\n')
        assert modal_credentials_present(_env(MODAL_CONFIG_PATH=str(config_path))) is True

    def test_modal_toml_without_tokens_does_not_count(self, tmp_path):
        config_path = tmp_path / "modal.toml"
        config_path.write_text("[profile]\nactive = true\n")
        assert modal_credentials_present(_env(MODAL_CONFIG_PATH=str(config_path))) is False

    def test_missing_config_file_does_not_count(self):
        assert modal_credentials_present(_env()) is False


class TestResolveTransport:
    def test_nothing_configured_resolves_to_none(self):
        assert resolve_transport(RemoteConfig.from_env(_env()), _env()) is None

    def test_health_url_alone_gives_http(self):
        env = _env(MODAL_KOKORO_HEALTH_URL="https://x.modal.run/health")
        assert resolve_transport(RemoteConfig.from_env(env), env) == "http"

    def test_credentials_prefer_the_sdk(self, monkeypatch):
        monkeypatch.setattr(modal_remote, "_modal_sdk_installed", lambda: True)
        env = _env(MODAL_TOKEN_ID="id", MODAL_TOKEN_SECRET="secret")
        assert resolve_transport(RemoteConfig.from_env(env), env) == "sdk"

    def test_credentials_without_the_sdk_fall_back_to_http(self, monkeypatch):
        monkeypatch.setattr(modal_remote, "_modal_sdk_installed", lambda: False)
        env = _env(
            MODAL_TOKEN_ID="id",
            MODAL_TOKEN_SECRET="secret",
            MODAL_KOKORO_HEALTH_URL="https://x.modal.run/health",
        )
        assert resolve_transport(RemoteConfig.from_env(env), env) == "http"

    def test_explicit_transport_is_honoured(self):
        env = _env(MODAL_KOKORO_TRANSPORT="sdk")
        assert resolve_transport(RemoteConfig.from_env(env), env) == "sdk"


# --------------------------------------------------------------------------
# The reachability probe behind /api/system/capabilities
# --------------------------------------------------------------------------


class TestProbe:
    def test_unconfigured_probe_reports_why(self):
        result = probe(env=_env())
        assert result["configured"] is False
        assert result["reachable"] is False
        assert "credentials" in result["error"]

    def test_successful_check_marks_reachable(self):
        result = probe(env=_env(MODAL_KOKORO_TRANSPORT="sdk"), checker=lambda: None)
        assert result["reachable"] is True
        assert result["error"] is None
        assert result["resolved_transport"] == "sdk"

    def test_failing_check_reports_the_reason(self):
        def checker():
            raise RemoteSynthesisError("app not found")

        result = probe(env=_env(MODAL_KOKORO_TRANSPORT="sdk"), checker=checker)
        assert result["reachable"] is False
        assert result["error"] == "app not found"

    def test_hanging_check_hits_the_probe_timeout(self):
        def checker():
            time.sleep(0.3)

        result = probe(env=_env(MODAL_KOKORO_TRANSPORT="sdk"), checker=checker, timeout_s=0.05)
        assert result["reachable"] is False
        assert "did not answer" in result["error"]

    def test_unexpected_checker_error_does_not_propagate(self):
        def checker():
            raise KeyError("boom")

        result = probe(env=_env(MODAL_KOKORO_TRANSPORT="sdk"), checker=checker)
        assert result["reachable"] is False
        assert "KeyError" in result["error"]

    def test_result_is_cached_until_forced(self):
        calls = []

        def checker():
            calls.append(1)

        probe(env=_env(MODAL_KOKORO_TRANSPORT="sdk"), checker=checker)
        probe(env=_env(MODAL_KOKORO_TRANSPORT="sdk"), checker=checker)
        assert len(calls) == 1
        probe(env=_env(MODAL_KOKORO_TRANSPORT="sdk"), checker=checker, force=True)
        assert len(calls) == 2

    def test_probe_never_leaks_token_values(self):
        env = _env(MODAL_TOKEN_ID="id", MODAL_TOKEN_SECRET="supersecret", MODAL_KOKORO_TRANSPORT="sdk")
        result = probe(env=env, checker=lambda: None)
        assert "supersecret" not in str(result)
