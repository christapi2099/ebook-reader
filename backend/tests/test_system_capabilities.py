"""Offline tests for the capability probe and the backend-selection logic.

These cover the parts of ``main._init_kokoro`` that used to be invisible: which
backend was chosen, what the device was, and why nothing was available when
Kokoro could not be built.
"""
import pytest
from fastapi.testclient import TestClient

import main as main_module
from main import app
from services import kokoro_runtime
from services.modal_remote import RemoteConfig, RemoteSynthesisError, reset_probe_cache

# ``conftest.isolate_from_real_resources`` replaces ``main._init_kokoro`` with a
# fake before every test (so no test loads the real model). Keep a reference to
# the genuine selector for the tests that are *about* the selection logic; it
# still resolves ``_init_local_kokoro`` / ``_init_remote_kokoro`` through the
# module globals, so those stay monkeypatchable.
_REAL_INIT_KOKORO = main_module._init_kokoro


@pytest.fixture(autouse=True)
def _clean_runtime():
    kokoro_runtime.runtime.reset()
    kokoro_runtime.runtime.requested_backend = "local"
    reset_probe_cache()  # the probe cache is process-wide; keep tests independent
    yield
    kokoro_runtime.runtime.reset()
    reset_probe_cache()


@pytest.fixture
def offline_remote(monkeypatch):
    """No Modal credentials, no health URL: the remote backend cannot be used."""
    monkeypatch.setattr(kokoro_runtime, "modal_credentials_present", lambda env=None: False)
    monkeypatch.setattr(kokoro_runtime, "resolve_transport", lambda config, env=None: None)
    monkeypatch.delenv("MODAL_KOKORO_TRANSPORT", raising=False)


class TestNormalizeBackend:
    @pytest.mark.parametrize("value", ["local", "LOCAL", " local ", "remote", "auto"])
    def test_supported_values_pass_through(self, value):
        assert kokoro_runtime.normalize_backend(value) in ("local", "remote", "auto")

    def test_missing_value_defaults_to_local(self):
        assert kokoro_runtime.normalize_backend(None) == "local"
        assert kokoro_runtime.normalize_backend("") == "local"

    def test_unknown_value_defaults_to_local(self):
        assert kokoro_runtime.normalize_backend("gpu-cluster") == "local"


class TestLocalProbe:
    def test_reports_real_torch_facts(self):
        info = kokoro_runtime.probe_local_torch()
        assert info["torch_version"]
        assert isinstance(info["cuda_available"], bool)
        assert isinstance(info["cuda_device_count"], int)
        assert "non-multiple" not in str(info["error"])

    def test_gpu_name_only_when_cuda_is_available(self):
        info = kokoro_runtime.probe_local_torch()
        if not info["cuda_available"]:
            assert info["gpu_name"] is None


class TestRuntimeState:
    def test_starts_with_no_backend(self):
        state = kokoro_runtime.KokoroRuntime()
        assert state.active_backend == "none"
        assert state.device is None

    def test_recording_a_local_pipeline_keeps_the_device(self):
        state = kokoro_runtime.KokoroRuntime()
        state.record_local("cuda", "hexgrad/Kokoro-82M")
        assert (state.active_backend, state.device) == ("local", "cuda")
        assert state.initialized_at is not None

    def test_recording_a_remote_backend_clears_the_error(self):
        state = kokoro_runtime.KokoroRuntime()
        state.record_failure("boom")
        state.record_remote()
        assert state.active_backend == "remote"
        assert state.error is None

    def test_recording_a_failure_explains_itself(self):
        state = kokoro_runtime.KokoroRuntime()
        state.record_failure("ValueError: no model")
        assert state.active_backend == "none"
        assert state.error == "ValueError: no model"


class TestCapabilities:
    def test_shape_and_sample_rate(self, offline_remote):
        payload = kokoro_runtime.capabilities(env={"MODAL_CONFIG_PATH": "/nonexistent/modal.toml"})
        assert payload["sample_rate"] == 24000
        assert payload["active_backend"] == "none"
        assert payload["synthesis_available"] is False
        assert set(payload) >= {"active_backend", "local", "remote", "errors", "sample_rate"}

    def test_reports_the_recorded_device(self, offline_remote):
        kokoro_runtime.runtime.record_local("cuda", "hexgrad/Kokoro-82M")
        payload = kokoro_runtime.capabilities(env={"MODAL_CONFIG_PATH": "/nonexistent/modal.toml"})
        assert payload["local"]["device_in_use"] == "cuda"
        assert payload["active_backend"] == "local"
        assert payload["synthesis_available"] is True

    def test_remote_reachability_comes_from_the_checker(self, offline_remote, monkeypatch):
        monkeypatch.setattr(
            kokoro_runtime,
            "resolve_transport",
            lambda config, env=None: "sdk",
        )
        env = {"MODAL_CONFIG_PATH": "/nonexistent/modal.toml", "MODAL_KOKORO_TRANSPORT": "sdk"}
        payload = kokoro_runtime.capabilities(env=env, checker=lambda: None)
        assert payload["remote"]["reachable"] is True
        assert payload["remote"]["resolved_transport"] == "sdk"

    def test_unreachable_remote_is_reported_not_hidden(self, offline_remote, monkeypatch):
        monkeypatch.setattr(kokoro_runtime, "resolve_transport", lambda config, env=None: "sdk")
        env = {"MODAL_CONFIG_PATH": "/nonexistent/modal.toml", "MODAL_KOKORO_TRANSPORT": "sdk"}

        def checker():
            raise RemoteSynthesisError("app not deployed")

        payload = kokoro_runtime.capabilities(env=env, checker=checker)
        assert payload["remote"]["reachable"] is False
        assert payload["remote"]["error"] == "app not deployed"

    def test_never_exposes_a_token(self, offline_remote, monkeypatch):
        monkeypatch.setattr(kokoro_runtime, "resolve_transport", lambda config, env=None: "sdk")
        env = {
            "MODAL_CONFIG_PATH": "/nonexistent/modal.toml",
            "MODAL_TOKEN_ID": "id",
            "MODAL_TOKEN_SECRET": "supersecret",
            "MODAL_KOKORO_TRANSPORT": "sdk",
        }
        payload = kokoro_runtime.capabilities(env=env, checker=lambda: None)
        assert "supersecret" not in str(payload)


class TestCapabilitiesEndpoint:
    """The HTTP surface. TestClient is used without its context manager so the
    lifespan (and therefore a real Kokoro load) does not run."""

    @pytest.fixture
    def client(self, offline_remote, monkeypatch):
        monkeypatch.setattr(
            kokoro_runtime,
            "probe_remote",
            lambda config=None, env=None, checker=None, force=False, timeout_s=None: {
                "configured": False,
                "reachable": False,
                "error": "not configured in tests",
                **RemoteConfig.from_env(env).describe(),
            },
        )
        return TestClient(app)

    def test_capabilities_returns_200(self, client):
        response = client.get("/api/system/capabilities")
        assert response.status_code == 200

    def test_capabilities_reports_probed_facts(self, client):
        body = client.get("/api/system/capabilities").json()
        assert body["local"]["torch_version"]
        assert body["sample_rate"] == 24000
        assert body["active_backend"] in ("local", "remote", "none")
        assert body["remote"]["reachable"] is False

    def test_health_keeps_its_original_shape(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"

    def test_health_reports_the_active_backend(self, client):
        body = client.get("/health").json()
        assert "backend" in body


class TestBackendSelection:
    """``_init_kokoro`` must never need the network, and must say why it failed."""

    def test_local_is_the_default_and_never_touches_the_remote(self, monkeypatch):
        monkeypatch.delenv("KOKORO_BACKEND", raising=False)
        sentinel = object()
        monkeypatch.setattr(main_module, "_init_local_kokoro", lambda: (sentinel, "cuda", None))

        def forbidden(requested):  # pragma: no cover - must not run
            raise AssertionError("local backend must not probe Modal")

        monkeypatch.setattr(main_module, "_init_remote_kokoro", forbidden)

        assert _REAL_INIT_KOKORO() is sentinel
        assert kokoro_runtime.runtime.active_backend == "local"
        assert kokoro_runtime.runtime.device == "cuda"
        assert kokoro_runtime.runtime.requested_backend == "local"

    def test_remote_failure_falls_back_to_local_and_records_the_reason(self, monkeypatch):
        monkeypatch.setenv("KOKORO_BACKEND", "remote")
        sentinel = object()
        monkeypatch.setattr(main_module, "_init_remote_kokoro", lambda requested: (None, "no credentials"))
        monkeypatch.setattr(main_module, "_init_local_kokoro", lambda: (sentinel, "cuda", None))

        assert _REAL_INIT_KOKORO() is sentinel
        assert kokoro_runtime.runtime.remote_error == "no credentials"
        assert kokoro_runtime.runtime.active_backend == "local"

    def test_remote_success_uses_the_remote_backend(self, monkeypatch):
        monkeypatch.setenv("KOKORO_BACKEND", "auto")
        from services.modal_remote import ModalKokoroClient

        client = ModalKokoroClient(config=RemoteConfig(transport="sdk"), invoke=lambda payload: {})
        monkeypatch.setattr(main_module, "_init_remote_kokoro", lambda requested: (client, None))

        def forbidden():  # pragma: no cover - must not run
            raise AssertionError("a reachable remote backend must not load the local model")

        monkeypatch.setattr(main_module, "_init_local_kokoro", forbidden)

        assert _REAL_INIT_KOKORO() is client
        assert kokoro_runtime.runtime.active_backend == "remote"

    def test_total_failure_is_recorded_not_silent(self, monkeypatch):
        monkeypatch.setenv("KOKORO_BACKEND", "local")
        monkeypatch.setattr(
            main_module, "_init_local_kokoro", lambda: (None, None, "ImportError: no torch")
        )

        assert _REAL_INIT_KOKORO() is None
        assert kokoro_runtime.runtime.active_backend == "none"
        assert kokoro_runtime.runtime.error == "ImportError: no torch"

    def test_local_init_reports_a_traceback_instead_of_a_silent_none(self, monkeypatch, caplog):
        """A broken local pipeline must log the real reason, with a traceback."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "kokoro":
                raise RuntimeError("no CUDA driver")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with caplog.at_level("ERROR", logger="main"):
            pipeline, device, error = main_module._init_local_kokoro()

        assert pipeline is None
        assert device is None
        assert error == "RuntimeError: no CUDA driver"
        assert "failed to initialise" in caplog.text
        assert "no CUDA driver" in caplog.text
