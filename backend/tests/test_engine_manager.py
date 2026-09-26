"""Tests for the runtime engine manager and the engine endpoints.

No torch, no Modal and no network: the builders and both probes are injected, so
these exercise the *decision* logic — which engine is built, what happens when a
build fails, what the selector is told, and what is persisted.
"""
import time
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from services import engine_manager, kokoro_runtime, modal_remote
from services.engine_manager import (
    CPU,
    GPU,
    MODAL,
    EngineBuildError,
    EngineManager,
    EngineUnavailable,
    UnknownEngine,
)


class FakeEngine:
    """Stands in for a KPipeline: enough to be swapped and identified."""

    def __init__(self, name: str) -> None:
        self.name = name


@dataclass
class Builders:
    """Recording builders for the local and remote engines, with a failure switch."""

    local_calls: list[str] = field(default_factory=list)
    remote_calls: list[int] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)

    def build_local(self, device: str):
        self.local_calls.append(device)
        if device in self.fail or "local" in self.fail:
            return None, f"RuntimeError: could not load on {device}"
        return FakeEngine(device), None

    def build_remote(self, require_reachable: bool, probe_timeout_s=None):
        self.remote_calls.append(1 if require_reachable else 0)
        if "modal" in self.fail:
            return None, "ConnectionError: no route to host"
        return modal_remote.ModalKokoroClient(
            config=modal_remote.RemoteConfig(transport="sdk"), invoke=lambda payload: {}
        ), None


def torch_info(*, cuda: bool, version: str | None = "2.14.0", error: str | None = None) -> dict:
    """A ``probe_local_torch()`` payload under test control."""
    return {
        "torch_version": version,
        "torch_cuda_version": "13.0" if cuda else None,
        "cuda_available": cuda,
        "cuda_device_count": 1 if cuda else 0,
        "gpu_name": "Fake GPU" if cuda else None,
        "error": error,
    }


def remote_info(*, reachable: bool, configured: bool = True, error: str | None = None) -> dict:
    """A ``modal_remote.probe()`` payload under test control."""
    return {
        "configured": configured,
        "reachable": reachable,
        "credentials_present": True,
        "error": error,
    }


def make_manager(builders, *, cuda: bool = True, remote: dict | None = None) -> EngineManager:
    """A manager whose builders *and* both probes are fakes.

    Defaults to a *reachable* Modal, because "can this machine switch to Modal?"
    is the question most of these tests ask; the unreachable case is asserted
    explicitly where it matters.
    """
    return EngineManager(
        local_builder=builders.build_local,
        remote_builder=builders.build_remote,
        torch_probe=lambda: torch_info(cuda=cuda),
        remote_probe=lambda **kwargs: remote_info(reachable=True) if remote is None else remote,
    )


@pytest.fixture
def builders():
    return Builders()


@pytest.fixture(autouse=True)
def _isolated_module_state(monkeypatch):
    """No persistence and no router fan-out unless a test asks for them."""
    monkeypatch.setattr(engine_manager, "load_persisted_engine", lambda: None)
    monkeypatch.setattr(engine_manager, "save_persisted_engine", lambda engine_id: None)
    engine_manager.register_applier(lambda engine: None)
    yield
    engine_manager.register_applier(lambda engine: None)


class TestSwitch:
    def test_cpu_and_gpu_ask_for_different_devices(self, builders):
        manager = make_manager(builders)
        manager.switch(CPU, persist=False)
        manager.switch(GPU, persist=False)
        assert builders.local_calls == ["cpu", "cuda"]

    def test_a_failed_build_leaves_the_previous_engine_live(self, builders):
        manager = make_manager(builders)
        first = manager.switch(CPU, persist=False)
        builders.fail.add("cuda")
        with pytest.raises(EngineBuildError, match="could not load on cuda"):
            manager.switch(GPU, persist=False)
        assert manager.current() is first
        assert manager.active() == CPU

    def test_the_failed_engine_is_not_recorded_as_active(self, builders):
        builders.fail.add("cuda")
        manager = make_manager(builders)
        with pytest.raises(EngineBuildError):
            manager.switch(GPU, persist=False)
        assert manager.active() is None

    def test_unknown_engine_is_rejected(self, builders):
        with pytest.raises(UnknownEngine, match="gpu-cluster"):
            make_manager(builders).switch("gpu-cluster", persist=False)

    def test_modal_switch_builds_a_client(self, builders):
        engine = make_manager(builders).switch(MODAL, persist=False, background_warmup=False)
        assert isinstance(engine, modal_remote.ModalKokoroClient)
        # require_reachable=False: an explicit request may target an app that is
        # not answering yet, and the first call reports that loudly.
        assert builders.remote_calls == [0]

    def test_switch_publishes_the_new_engine(self, builders):
        seen = []
        engine_manager.register_applier(seen.append)
        make_manager(builders).switch(CPU, persist=False)
        assert [engine.name for engine in seen] == ["cpu"]

    def test_switch_persists_through_the_injected_saver(self, builders, monkeypatch):
        saved = []
        monkeypatch.setattr(engine_manager, "save_persisted_engine", saved.append)
        make_manager(builders).switch(GPU)
        assert saved == [GPU]

    def test_a_failed_switch_persists_nothing(self, builders, monkeypatch):
        saved = []
        monkeypatch.setattr(engine_manager, "save_persisted_engine", saved.append)
        builders.fail.add("cuda")
        with pytest.raises(EngineBuildError):
            make_manager(builders).switch(GPU)
        assert saved == []

    def test_switch_marks_the_runtime_state(self, builders):
        manager = make_manager(builders)
        manager.switch(GPU, persist=False)
        state = kokoro_runtime.runtime
        assert (state.active_backend, state.device) == ("local", "cuda")
        manager.switch(MODAL, persist=False, background_warmup=False)
        assert state.active_backend == "remote"
        assert state.device is None


class TestOptions:
    def test_reasons_come_from_the_probes(self, builders):
        manager = make_manager(
            builders, cuda=False, remote=remote_info(reachable=False, configured=False)
        )
        options = {option.id: option for option in manager.options()}
        assert options[CPU].available is True
        assert options[GPU].available is False
        assert options[GPU].reason == "No CUDA GPU detected"
        assert options[MODAL].available is False
        assert options[MODAL].reason == "Modal not set up"

    def test_unreachable_modal_keeps_the_probe_reason(self, builders):
        manager = make_manager(
            builders,
            cuda=True,
            remote=remote_info(reachable=False, error="Network error probing https://x"),
        )
        option = next(o for o in manager.options() if o.id == MODAL)
        assert option.available is False
        assert option.reason.startswith("Modal unreachable — ")
        assert "Network error" in option.reason

    def test_missing_torch_disables_both_local_cards(self, builders):
        manager = EngineManager(
            local_builder=builders.build_local,
            remote_builder=builders.build_remote,
            torch_probe=lambda: torch_info(cuda=False, version=None, error="ImportError: no torch"),
            remote_probe=lambda **kwargs: remote_info(reachable=False, configured=False),
        )
        options = {option.id: option for option in manager.options()}
        assert options[CPU].available is False
        assert "no torch" in options[CPU].reason
        assert options[GPU].available is False

    def test_an_unavailable_engine_cannot_be_selected(self, builders):
        manager = make_manager(builders, cuda=False, remote=remote_info(reachable=False))
        with pytest.raises(EngineUnavailable) as excinfo:
            manager.switch(GPU, persist=False)
        assert excinfo.value.reason == "No CUDA GPU detected"

    def test_state_reports_selected_active_and_options(self, builders, monkeypatch):
        monkeypatch.setattr(engine_manager, "load_persisted_engine", lambda: MODAL)
        manager = make_manager(builders, cuda=True, remote=remote_info(reachable=True))
        manager.switch(CPU, persist=False)
        state = manager.state()
        assert state["selected"] == MODAL
        assert state["active"] == CPU
        assert state["switching"] is False
        assert {o["id"] for o in state["options"]} == {CPU, GPU, MODAL}

    def test_the_selector_and_the_switch_use_the_same_probe(self, builders):
        """A machine with no GPU must not be offered the GPU card."""
        manager = make_manager(builders, cuda=False)
        assert next(o for o in manager.options() if o.id == GPU).available is False
        with pytest.raises(EngineUnavailable):
            manager.switch(GPU, persist=False)


class TestStartup:
    def test_local_env_prefers_the_gpu_when_there_is_one(self, builders):
        assert make_manager(builders, cuda=True).startup("local").name == "cuda"
        assert builders.local_calls == ["cuda"]

    def test_local_env_falls_back_to_cpu_without_cuda(self, builders):
        assert make_manager(builders, cuda=False).startup("local").name == "cpu"
        assert builders.local_calls == ["cpu"]

    def test_local_env_with_a_broken_gpu_still_starts_on_cpu(self, builders):
        builders.fail.add("cuda")
        assert make_manager(builders, cuda=True).startup("local").name == "cpu"
        assert builders.local_calls == ["cuda", "cpu"]

    def test_remote_env_uses_modal(self, builders):
        engine = make_manager(builders).startup("remote")
        assert isinstance(engine, modal_remote.ModalKokoroClient)

    def test_remote_env_falls_back_to_local_when_modal_fails(self, builders):
        builders.fail.add("modal")
        assert make_manager(builders, cuda=True).startup("remote").name == "cuda"
        assert kokoro_runtime.runtime.active_backend == "local"

    def test_auto_uses_modal_only_when_reachable(self, builders):
        manager = make_manager(builders, remote=remote_info(reachable=True))
        assert isinstance(manager.startup("auto"), modal_remote.ModalKokoroClient)

    def test_auto_stays_local_when_modal_is_unreachable(self, builders):
        manager = make_manager(builders, cuda=True, remote=remote_info(reachable=False))
        assert manager.startup("auto").name == "cuda"

    def test_the_persisted_choice_wins_over_the_env(self, builders, monkeypatch):
        monkeypatch.setattr(engine_manager, "load_persisted_engine", lambda: CPU)
        assert make_manager(builders).startup("remote").name == "cpu"

    def test_an_unusable_persisted_choice_falls_through(self, builders, monkeypatch):
        monkeypatch.setattr(engine_manager, "load_persisted_engine", lambda: CPU)
        builders.fail.add("local")
        assert isinstance(
            make_manager(builders).startup("remote"), modal_remote.ModalKokoroClient
        )

    def test_total_failure_is_recorded_and_does_not_raise(self, builders):
        builders.fail.update({"local", "modal", "cpu", "cuda"})
        assert make_manager(builders, cuda=False).startup("local") is None
        state = kokoro_runtime.runtime
        assert state.active_backend == "none"
        assert state.error == "No Kokoro backend available"


class TestPlaybackPhase:
    def test_local_engine_has_nothing_to_report(self, builders):
        manager = make_manager(builders)
        manager.switch(CPU, persist=False)
        assert manager.playback_phase() is None

    def test_cold_modal_container_reports_starting(self, builders):
        manager = make_manager(builders)
        manager.switch(MODAL, persist=False, background_warmup=False)
        assert manager.playback_phase() == engine_manager.PHASE_STARTING

    def test_a_warm_container_is_silent(self, builders, monkeypatch):
        manager = make_manager(builders)
        engine = manager.switch(MODAL, persist=False, background_warmup=False)
        monkeypatch.setattr(engine, "is_warm", lambda: True)
        assert manager.playback_phase() is None

    def test_tracked_warming_up_is_reported(self, builders):
        manager = make_manager(builders)
        manager.switch(MODAL, persist=False, background_warmup=False)
        manager._phase = engine_manager.PHASE_WARMING_UP
        assert manager.playback_phase() == engine_manager.PHASE_WARMING_UP


class TestWarmupWatcher:
    def _wait_for_ready(self, manager) -> None:
        deadline = time.monotonic() + 5
        while manager.phase != engine_manager.PHASE_READY and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_warmup_reports_ready_once_the_call_answers(self, builders, monkeypatch):
        manager = make_manager(builders)
        engine = manager.switch(MODAL, persist=False, background_warmup=False)
        monkeypatch.setattr(engine, "runner_count", lambda: 1)
        monkeypatch.setattr(type(engine), "__call__", lambda self, *a, **k: [])
        manager._start_warmup(engine)
        self._wait_for_ready(manager)
        assert manager.phase == engine_manager.PHASE_READY

    def test_a_failing_warmup_still_reaches_ready(self, builders, monkeypatch):
        """A warm-up that fails must not leave the UI spinning for ever."""
        manager = make_manager(builders)
        engine = manager.switch(MODAL, persist=False, background_warmup=False)

        def boom(self, *args, **kwargs):
            raise RuntimeError("container exploded")

        monkeypatch.setattr(type(engine), "__call__", boom)
        manager._start_warmup(engine)
        self._wait_for_ready(manager)
        assert manager.phase == engine_manager.PHASE_READY


class TestFailureMessages:
    def test_phase_is_named_in_the_message(self):
        message = engine_manager.phase_failure_message(
            engine_manager.PHASE_WARMING_UP, "container died"
        )
        assert message == "Failed while warming up the GPU · container died"

    def test_every_export_phase_has_a_label(self):
        for phase in (
            engine_manager.PHASE_UPLOADING,
            engine_manager.PHASE_STARTING,
            engine_manager.PHASE_PROCESSING,
            engine_manager.PHASE_ENCODING,
        ):
            assert phase in engine_manager.PHASE_FAILURE_LABELS

    def test_a_phase_less_failure_is_just_the_reason(self):
        assert engine_manager.phase_failure_message(None, "boom") == "boom"


@pytest.fixture
def engine_client(monkeypatch):
    """TestClient over the real app, with an injected manager."""
    import main

    manager = make_manager(Builders(), cuda=False, remote=remote_info(reachable=True))
    monkeypatch.setattr(engine_manager, "manager", manager)
    return TestClient(main.app), manager


class TestEngineEndpoints:
    def test_get_engine_lists_the_three_engines(self, engine_client):
        test_client, _ = engine_client
        body = test_client.get("/api/system/engine").json()
        assert [o["id"] for o in body["options"]] == [CPU, GPU, MODAL]
        assert body["active"] is None

    def test_post_switches_and_reports_the_new_engine(self, engine_client):
        test_client, _ = engine_client
        response = test_client.post("/api/system/engine", json={"engine": CPU})
        assert response.status_code == 200
        assert response.json()["active"] == CPU

    def test_unknown_engine_is_a_400(self, engine_client):
        test_client, _ = engine_client
        response = test_client.post("/api/system/engine", json={"engine": "quantum"})
        assert response.status_code == 400
        assert "quantum" in response.json()["detail"]

    def test_unavailable_engine_is_a_409_with_the_probe_reason(self, engine_client):
        test_client, _ = engine_client
        response = test_client.post("/api/system/engine", json={"engine": GPU})
        assert response.status_code == 409
        assert response.json()["detail"] == "No CUDA GPU detected"

    def test_a_failed_build_is_a_503_with_the_build_error(self, engine_client, monkeypatch):
        test_client, manager = engine_client
        monkeypatch.setattr(
            manager, "_local_builder", lambda device: (None, "RuntimeError: cuda out of memory")
        )
        response = test_client.post("/api/system/engine", json={"engine": CPU})
        assert response.status_code == 503
        assert "cuda out of memory" in response.json()["detail"]

    def test_a_failed_switch_keeps_the_previous_engine(self, engine_client, monkeypatch):
        test_client, manager = engine_client
        test_client.post("/api/system/engine", json={"engine": CPU})
        monkeypatch.setattr(manager, "_local_builder", lambda device: (None, "RuntimeError: nope"))
        monkeypatch.setattr(manager, "_torch_probe", lambda: torch_info(cuda=True))
        assert test_client.post("/api/system/engine", json={"engine": GPU}).status_code == 503
        assert test_client.get("/api/system/engine").json()["active"] == CPU

    def test_capabilities_still_works(self, engine_client):
        test_client, _ = engine_client
        assert test_client.get("/api/system/capabilities").status_code == 200
