"""Offline tests for the capability probe and the backend-selection logic.

These cover the parts of ``main._init_kokoro`` that used to be invisible: which
backend was chosen, what the device was, and why nothing was available when
Kokoro could not be built.

Two kinds of test live here, and the difference matters:

* ``TestLocalProbe`` has tests that read the *real* machine (is torch installed,
  is a GPU visible). They are the reason ``probe_local_torch`` is honest, and
  they are skipped, explicitly, on a machine that has no torch -- the suite must
  not go red merely because a GPU or a wheel is absent.
* Everything else pins those inputs. The probe's mapping (CUDA present, absent,
  raising; torch missing) is asserted against an injected ``torch`` module, so
  the coverage does not evaporate on the machine where the live test is skipped.
"""
import importlib.util
import sys
import types

import pytest
from fastapi.testclient import TestClient

import main as main_module
from main import app
from services import engine_manager, kokoro_runtime
from services.modal_remote import RemoteConfig, RemoteSynthesisError, reset_probe_cache

TORCH_INSTALLED = importlib.util.find_spec("torch") is not None
needs_torch = pytest.mark.skipif(
    not TORCH_INSTALLED, reason="torch is not installed in this environment"
)

# ``conftest.isolate_from_real_resources`` replaces ``main._init_kokoro`` with a
# fake before every test (so no test loads the real model). Keep a reference to
# the genuine selector for the tests that are *about* the selection logic; it is
# a thin call into ``engine_manager.manager.startup``, which those tests
# monkeypatch on the manager rather than through this module.
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


def _fake_torch(*, cuda: bool, version: str = "2.14.0", cuda_version: str = "13.0",
                raise_on_probe: str | None = None):
    """A ``torch`` module with exactly the surface ``probe_local_torch`` reads."""
    module = types.ModuleType("torch")
    module.__version__ = version
    module.version = types.SimpleNamespace(cuda=cuda_version if cuda else None)

    def is_available():
        if raise_on_probe:
            raise RuntimeError(raise_on_probe)
        return cuda

    module.cuda = types.SimpleNamespace(
        is_available=is_available,
        device_count=lambda: 1 if cuda else 0,
        get_device_name=lambda index: "Fake GPU",
    )
    return module


@pytest.fixture
def fake_torch(monkeypatch):
    """Install an injected ``torch`` so the probe's mapping can be pinned.

    ``probe_local_torch`` imports torch inside the function, so a ``sys.modules``
    entry is all it takes. This is what makes the probe's branches testable on a
    machine with no torch, no CUDA driver and no GPU.
    """

    def install(*, cuda: bool, **kwargs):
        monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=cuda, **kwargs))

    return install


class TestLocalProbe:
    @needs_torch
    def test_reports_real_torch_facts(self):
        info = kokoro_runtime.probe_local_torch()
        assert info["torch_version"]
        assert isinstance(info["cuda_available"], bool)
        assert isinstance(info["cuda_device_count"], int)
        assert "non-multiple" not in str(info["error"])

    @needs_torch
    def test_gpu_name_only_when_cuda_is_available(self):
        info = kokoro_runtime.probe_local_torch()
        if not info["cuda_available"]:
            assert info["gpu_name"] is None
        else:
            assert info["gpu_name"], "CUDA is available but no device name was reported"


class TestLocalProbeMapping:
    """The probe's contract, pinned: no GPU, no driver, no torch required."""

    def test_a_visible_gpu_is_reported_with_its_name_and_count(self, fake_torch):
        fake_torch(cuda=True)
        info = kokoro_runtime.probe_local_torch()
        assert info["torch_version"] == "2.14.0"
        assert info["torch_cuda_version"] == "13.0"
        assert info["cuda_available"] is True
        assert info["cuda_device_count"] == 1
        assert info["gpu_name"] == "Fake GPU"
        assert info["error"] is None

    def test_no_gpu_reports_zero_devices_and_no_name(self, fake_torch):
        fake_torch(cuda=False)
        info = kokoro_runtime.probe_local_torch()
        assert info["cuda_available"] is False
        assert info["cuda_device_count"] == 0
        assert info["gpu_name"] is None
        assert info["error"] is None

    def test_a_broken_cuda_probe_is_reported_not_raised(self, fake_torch):
        """An NVML failure must become an `error` string, never an exception."""
        fake_torch(cuda=True, raise_on_probe="Can't initialize NVML")
        info = kokoro_runtime.probe_local_torch()
        assert info["cuda_available"] is False
        assert info["error"] == "RuntimeError: Can't initialize NVML"

    def test_a_missing_torch_is_reported_not_raised(self, monkeypatch):
        # `None` in sys.modules makes `import torch` raise, which is the branch a
        # machine without the wheel takes. ModuleNotFoundError is what CPython
        # actually raises here, and it is an ImportError subclass.
        monkeypatch.setitem(sys.modules, "torch", None)
        info = kokoro_runtime.probe_local_torch()
        assert info["torch_version"] is None
        assert info["cuda_available"] is False
        assert info["error"] == "ModuleNotFoundError: import of torch halted; None in sys.modules"


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
    def client(self, offline_remote, monkeypatch, fake_torch):
        # Both live machine inputs are pinned: no Modal (offline_remote) and a
        # torch that reports no GPU, so this class asserts the endpoint's wiring
        # rather than the host's hardware.
        fake_torch(cuda=False)
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
        assert body["local"]["torch_version"] == "2.14.0"
        assert body["local"]["cuda_available"] is False
        assert body["local"]["gpu_name"] is None
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
    """``main._init_kokoro`` is now a thin call into the engine manager.

    The selection logic itself (env mapping, fallbacks, persistence precedence)
    is covered in depth by ``test_engine_manager.py``; what is asserted here is
    the wiring — that startup really goes through the manager, and that the
    logged reason survives the move.
    """

    def test_init_kokoro_returns_whatever_the_manager_built(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(engine_manager.manager, "startup", lambda env_backend=None: sentinel)
        assert _REAL_INIT_KOKORO() is sentinel

    def test_init_kokoro_passes_the_env_var_through(self, monkeypatch):
        seen = {}
        monkeypatch.setenv("KOKORO_BACKEND", "remote")

        def record(env_backend=None):
            seen["env"] = env_backend
            return None

        monkeypatch.setattr(engine_manager.manager, "startup", record)
        _REAL_INIT_KOKORO()
        assert seen["env"] == "remote"

    def test_default_is_local_and_never_probes_the_remote(self, monkeypatch):
        """KOKORO_BACKEND unset must not cost a network round-trip."""
        monkeypatch.delenv("KOKORO_BACKEND", raising=False)
        monkeypatch.setattr(engine_manager, "load_persisted_engine", lambda: None)

        def forbidden(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the local backend must not probe Modal")

        manager = engine_manager.EngineManager(
            local_builder=lambda device: (object(), None),
            remote_builder=forbidden,
            torch_probe=lambda: {"torch_version": "2.14.0", "cuda_available": False, "error": None},
            remote_probe=forbidden,
        )
        assert manager.startup(None) is not None
        assert kokoro_runtime.runtime.active_backend == "local"

    def test_total_failure_is_recorded_not_silent(self, monkeypatch):
        manager = engine_manager.EngineManager(
            local_builder=lambda device: (None, "ImportError: no torch"),
            remote_builder=lambda *args, **kwargs: (None, "no credentials"),
            torch_probe=lambda: {"torch_version": None, "cuda_available": False, "error": "no torch"},
            remote_probe=lambda **kwargs: {"configured": False, "reachable": False},
        )
        monkeypatch.setattr(engine_manager, "load_persisted_engine", lambda: None)
        assert manager.startup("local") is None
        assert kokoro_runtime.runtime.active_backend == "none"
        assert kokoro_runtime.runtime.error == "No Kokoro backend available"

    def test_local_init_reports_a_traceback_instead_of_a_silent_none(self, monkeypatch, caplog):
        """A broken local pipeline must log the real reason, with a traceback."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "kokoro":
                raise RuntimeError("no CUDA driver")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with caplog.at_level("ERROR", logger="services.engine_manager"):
            pipeline, error = engine_manager.build_local("cpu")

        assert pipeline is None
        assert error == "RuntimeError: no CUDA driver"
        assert "failed to initialise" in caplog.text
        assert "no CUDA driver" in caplog.text
