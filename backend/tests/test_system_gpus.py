"""/api/system/gpus: cached inventory, background probe, selection.

The real subprocess probe, sysfs and /dev are replaced by fakes; the request path
must never import or call torch.
"""
import sys
import threading
import time
import types

import pytest

from routers import system as system_router
from services import engine_manager, gpu_inventory


def _hw(card, vendor, pci, vram=12288):
    return {
        "card": card, "vendor": vendor, "pci_bus_id": pci, "name": f"{vendor} {card}",
        "vram_mb": vram, "render_nodes": ["renderD128"], "vendor_hex": "0x1002",
        "device_hex": "0x73df",
    }


AMD = _hw("card0", "amd", "0000:03:00.0")
NV = _hw("card1", "nvidia", "0000:04:00.0", 8192)


def _probe_ok(build, devs):
    return {"ok": True, "build": build, "torch_version": "2.9.0", "devices": devs}


@pytest.fixture
def gpu(monkeypatch, client):
    """Fake hardware + probe; returns a controller. Resets module state."""
    calls = {"probe": 0}
    ctl = types.SimpleNamespace(hw=[AMD], result=_probe_ok("rocm", [
        {"index": 0, "name": "RX 6700 XT", "total_memory": 12 << 30, "pci_bus_id": "0000:03:00.0"}]),
        calls=calls, gate=None, access=None)

    def runner(timeout_s=15.0):
        calls["probe"] += 1
        if ctl.gate is not None:
            ctl.gate.wait(5)
        return ctl.result

    monkeypatch.setattr(gpu_inventory, "scan_hardware", lambda *a, **k: [dict(h) for h in ctl.hw])
    monkeypatch.setattr(gpu_inventory, "run_probe_subprocess", runner)
    monkeypatch.setattr(gpu_inventory, "check_access", lambda *a, **k: ctl.access)
    monkeypatch.setattr(system_router, "_gpu_names", {})
    monkeypatch.setattr(system_router, "_gpu_probing", False)
    monkeypatch.setattr(system_router, "_gpu_timed_out_at", None)
    gpu_inventory.invalidate()
    yield ctl
    gpu_inventory.invalidate()


def _wait_idle():
    deadline = time.time() + 5
    while system_router._gpu_probing and time.time() < deadline:
        time.sleep(0.01)
    assert not system_router._gpu_probing


def _settled(client):
    client.get("/api/system/gpus")
    _wait_idle()
    return client.get("/api/system/gpus").json()


def test_validating_then_ready(client, gpu):
    first = client.get("/api/system/gpus").json()
    assert first["state"] == "validating"
    assert first["devices"][0]["usable"] is None
    assert first["devices"][0]["reason"] == "validating"
    assert first["devices"][0]["vendor_label"] == "AMD"
    _wait_idle()
    body = client.get("/api/system/gpus").json()
    assert body["state"] == "ready"
    d = body["devices"][0]
    assert d["usable"] is True and d["backend"] == "rocm" and d["vram_mb"] == 12288
    assert set(d) == {"id", "vendor", "vendor_label", "name", "backend", "usable", "reason", "vram_mb"}
    assert body["default"] == d["id"] == "pci:0000:03:00.0"
    assert body["needs_choice"] is False
    assert body["torch_build"] == "rocm"
    assert body["selected"] is None


def test_unusable_reports_reason(client, gpu):
    gpu.result = _probe_ok("cuda", [])
    body = _settled(client)
    d = body["devices"][0]
    assert d["usable"] is False and "ROCm" in d["reason"]
    assert body["default"] is None and body["torch_build"] == "cuda"


def test_no_hardware_is_ready_and_never_probes(client, gpu):
    gpu.hw = []
    body = client.get("/api/system/gpus").json()
    assert body["state"] == "ready" and body["devices"] == []
    assert gpu.calls["probe"] == 0


def test_two_usable_needs_choice(client, gpu):
    gpu.hw = [AMD, NV]
    gpu.result = _probe_ok("cuda", [])
    # Make both usable: fake a join where backends match by patching the vendor map.
    real_join = gpu_inventory.join

    def join(hw, probe, dev_root=gpu_inventory.DEV_ROOT):
        inv = real_join(hw, probe, dev_root)
        devs = [gpu_inventory.Device(**{**d.__dict__, "usable": True, "reason": None, "torch_index": i})
                for i, d in enumerate(inv.devices)]
        return gpu_inventory.Inventory(devs, inv.torch_build, inv.torch_version, inv.probe_error)

    system_router.gpu_inventory.join = join
    try:
        body = _settled(client)
    finally:
        system_router.gpu_inventory.join = real_join
    assert body["needs_choice"] is True
    assert body["default"] == "pci:0000:04:00.0"  # cuda preferred over rocm


def test_concurrent_gets_start_exactly_one_probe(client, gpu):
    gpu.gate = threading.Event()
    results = []

    def hit():
        results.append(client.get("/api/system/gpus").json()["state"])

    threads = [threading.Thread(target=hit) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == ["validating"] * 8
    gpu.gate.set()
    _wait_idle()
    assert gpu.calls["probe"] == 1
    assert client.get("/api/system/gpus").json()["state"] == "ready"
    assert gpu.calls["probe"] == 1  # cached afterwards


def test_timed_out_is_not_cached_and_rate_limited(client, gpu, monkeypatch):
    gpu.result = {"ok": False, "error": gpu_inventory.PROBE_TIMED_OUT, "timed_out": True}
    body = _settled(client)
    assert body["state"] == "timed_out"
    assert body["devices"][0]["usable"] is False
    assert gpu.calls["probe"] == 1
    for _ in range(5):  # hammering inside the window does not re-probe
        assert client.get("/api/system/gpus").json()["state"] == "timed_out"
    assert gpu.calls["probe"] == 1
    # Window elapsed: the next GET retries, and a healthy probe recovers.
    monkeypatch.setattr(system_router, "_gpu_timed_out_at",
                        time.monotonic() - system_router.GPU_TIMEOUT_RETRY_S - 1)
    gpu.result = _probe_ok("rocm", [
        {"index": 0, "name": "x", "total_memory": 1 << 30, "pci_bus_id": "0000:03:00.0"}])
    assert client.get("/api/system/gpus").json()["state"] == "validating"
    _wait_idle()
    assert gpu.calls["probe"] == 2
    assert client.get("/api/system/gpus").json()["state"] == "ready"


def test_probe_failure_is_error_state(client, gpu):
    gpu.result = {"ok": False, "error": "ImportError: boom"}
    body = _settled(client)
    assert body["state"] == "error"
    assert "boom" in body["devices"][0]["reason"]


def test_refresh_invalidates_and_reprobes(client, gpu):
    assert _settled(client)["state"] == "ready"
    assert gpu.calls["probe"] == 1
    body = client.post("/api/system/gpus/refresh").json()
    assert body["state"] == "validating"
    _wait_idle()
    assert gpu.calls["probe"] == 2
    assert client.get("/api/system/gpus").json()["state"] == "ready"


def test_put_unknown_id_is_400(client, gpu):
    _settled(client)
    r = client.put("/api/system/gpus", json={"device_id": "pci:dead"})
    assert r.status_code == 400 and "Unknown GPU" in r.json()["detail"]
    assert engine_manager.load_persisted_gpu_device() is None


def test_put_unusable_is_409_with_reason(client, gpu):
    gpu.result = _probe_ok("cuda", [])
    _settled(client)
    r = client.put("/api/system/gpus", json={"device_id": "pci:0000:03:00.0"})
    assert r.status_code == 409 and "ROCm" in r.json()["detail"]
    assert engine_manager.load_persisted_gpu_device() is None


def test_put_while_validating_is_409(client, gpu):
    gpu.gate = threading.Event()
    client.get("/api/system/gpus")
    r = client.put("/api/system/gpus", json={"device_id": "pci:0000:03:00.0"})
    assert r.status_code == 409 and "validation" in r.json()["detail"]
    gpu.gate.set()
    _wait_idle()


def test_put_persists_reports_restart_and_null_clears(client, gpu, monkeypatch):
    _settled(client)
    live = engine_manager.manager
    monkeypatch.setattr(live, "request_switch",
                        lambda *a, **k: pytest.fail("PUT must not touch the engine"))
    before = (live.state()["active"], live.state()["selected"])
    r = client.put("/api/system/gpus", json={"device_id": "pci:0000:03:00.0"})
    assert r.status_code == 200
    body = r.json()
    assert body["restart_required"] is True
    assert body["selected"] == "pci:0000:03:00.0"
    assert engine_manager.load_persisted_gpu_device() == "pci:0000:03:00.0"
    assert client.get("/api/system/gpus").json()["selected"] == "pci:0000:03:00.0"
    assert (live.state()["active"], live.state()["selected"]) == before
    r = client.put("/api/system/gpus", json={"device_id": None})
    assert r.status_code == 200 and r.json()["selected"] is None
    assert engine_manager.load_persisted_gpu_device() is None


def test_request_path_never_touches_torch(client, gpu, monkeypatch):
    class Trap(types.ModuleType):
        def __getattr__(self, name):
            raise AssertionError(f"torch.{name} touched in the API process")

    monkeypatch.setitem(sys.modules, "torch", Trap("torch"))
    monkeypatch.delitem(sys.modules, "torch.cuda", raising=False)
    assert client.get("/api/system/gpus").status_code == 200
    _wait_idle()
    assert client.get("/api/system/gpus").json()["state"] == "ready"
    assert client.post("/api/system/gpus/refresh").status_code == 200
    _wait_idle()
    assert client.put("/api/system/gpus", json={"device_id": None}).status_code == 200
