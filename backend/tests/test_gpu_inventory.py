"""Fake-sysfs / fake-probe tests for services/gpu_inventory.py (no torch, no GPU)."""
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from services import engine_manager, gpu_inventory as gi, kokoro_runtime  # noqa: E402

VENDOR_HEX = {"nvidia": "0x10de", "amd": "0x1002", "intel": "0x8086"}
NO_LSPCI = lambda _pci: None  # noqa: E731


@pytest.fixture(autouse=True)
def _fresh_cache():
    gi.invalidate()
    yield
    gi.invalidate()


def make_card(root: Path, n: int, vendor: str, pci: str, *, cls="0x030000", driver=None, vram=None):
    pci_dir = root / "_pci" / pci
    pci_dir.mkdir(parents=True, exist_ok=True)
    (pci_dir / "vendor").write_text(VENDOR_HEX.get(vendor, vendor) + "\n")
    (pci_dir / "device").write_text("0x73df\n")
    (pci_dir / "class").write_text(cls + "\n")
    if driver:
        (pci_dir / "uevent").write_text(f"DRIVER={driver}\n")
    if vram:
        (pci_dir / "mem_info_vram_total").write_text(str(vram))
    (pci_dir / "drm" / f"renderD{128 + n}").mkdir(parents=True, exist_ok=True)
    card = root / f"card{n}"
    card.mkdir(parents=True, exist_ok=True)
    if not (card / "device").exists():
        (card / "device").symlink_to(pci_dir)
    return card


def make_dev(tmp_path: Path, *, kfd=True, render=(128, 129, 130), mode=0o666) -> Path:
    dev = tmp_path / "dev"
    (dev / "dri").mkdir(parents=True, exist_ok=True)
    if kfd:
        (dev / "kfd").write_text("")
        (dev / "kfd").chmod(mode)
    for n in render:
        (dev / "dri" / f"renderD{n}").write_text("")
        (dev / "dri" / f"renderD{n}").chmod(mode)
    return dev


def probe_of(build, devices=(), version="2.14.0"):
    return lambda timeout_s: {"ok": True, "build": build, "torch_version": version, "devices": list(devices)}


def tdev(index, pci, name="Torch GPU", mem=8 * 1024**3):
    return {"index": index, "name": name, "total_memory": mem, "pci_bus_id": pci}


def inventory(tmp_path, cards, probe, **kw):
    sysfs = tmp_path / "drm"
    sysfs.mkdir(exist_ok=True)
    for i, (vendor, pci) in enumerate(cards):
        make_card(sysfs, i, vendor, pci)
    if "dev_root" not in kw:
        kw["dev_root"] = make_dev(tmp_path)
    return gi.get_inventory(sysfs_root=sysfs, probe_runner=probe, lspci=NO_LSPCI, env={}, **kw)


# ---- import hygiene ---------------------------------------------------------

def test_importing_gpu_inventory_does_not_import_torch():
    script = f"import sys; sys.path.insert(0, {str(BACKEND_DIR)!r}); import services.gpu_inventory; print('torch' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "False", out.stderr


# ---- layer A ----------------------------------------------------------------

def test_sysfs_ignores_connectors_virtual_and_non_display(tmp_path):
    sysfs = tmp_path / "drm"
    sysfs.mkdir()
    make_card(sysfs, 0, "amd", "0000:03:00.0")
    (sysfs / "card0-DP-1").mkdir()
    make_card(sysfs, 1, "amd", "0000:04:00.0", driver="vkms")
    make_card(sysfs, 2, "amd", "0000:05:00.0", driver="simpledrm")
    make_card(sysfs, 3, "0x1a03", "0000:06:00.0")  # BMC (ASPEED)
    make_card(sysfs, 4, "nvidia", "0000:07:00.0", cls="0x040300")  # not display
    hw = gi.scan_hardware(sysfs, NO_LSPCI)
    assert [h["card"] for h in hw] == ["card0"]
    assert hw[0]["pci_bus_id"] == "0000:03:00.0"


def test_missing_sysfs_root_is_empty_not_error(tmp_path):
    inv = gi.get_inventory(sysfs_root=tmp_path / "nope", probe_runner=probe_of("cpu"), env={})
    assert inv.devices == []


def test_no_devices_never_runs_the_probe(tmp_path):
    def boom(_t):
        raise AssertionError("probe must not run without hardware")
    sysfs = tmp_path / "drm"
    sysfs.mkdir()
    assert gi.get_inventory(sysfs_root=sysfs, probe_runner=boom, env={}).devices == []


def test_vram_from_sysfs_when_torch_has_no_device(tmp_path):
    sysfs = tmp_path / "drm"
    sysfs.mkdir()
    make_card(sysfs, 0, "amd", "0000:03:00.0", vram=4 * 1024**3)
    assert gi.scan_hardware(sysfs, NO_LSPCI)[0]["vram_mb"] == 4096


# ---- matrix: vendor x build x count ----------------------------------------

BUILD_FOR = {"nvidia": "cuda", "amd": "rocm", "intel": "xpu"}
PCI = ["0000:1a:00.0", "0000:2b:00.0"]


@pytest.mark.parametrize("vendor", ["nvidia", "amd", "intel"])
@pytest.mark.parametrize("build", ["cuda", "rocm", "xpu", "cpu"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_matrix(tmp_path, vendor, build, count):
    cards = [(vendor, PCI[i]) for i in range(count)]
    tdevs = [tdev(i, PCI[i].upper()) for i in range(count)] if build != "cpu" else []
    inv = inventory(tmp_path, cards, probe_of(build, tdevs))
    assert len(inv.devices) == count
    for d in inv.devices:
        assert d.id == f"pci:{d.pci_bus_id}"
        if build == BUILD_FOR[vendor]:
            assert d.usable and d.reason is None
            assert d.torch_index == PCI.index(d.pci_bus_id)
        else:
            assert not d.usable and d.torch_index is None
            assert d.reason
    if count:
        assert inv.torch_build == build


def test_amd_with_cuda_build_reason_is_actionable(tmp_path):
    inv = inventory(tmp_path, [("amd", PCI[0])], probe_of("cuda"))
    r = inv.devices[0].reason
    assert "CUDA build" in r and "ROCm torch environment" in r
    assert "scripts/setup-rocm-env.sh" in r and "planned" in r


def test_nvidia_with_cpu_or_rocm_and_intel_without_xpu(tmp_path):
    assert "needs a CUDA build" in inventory(tmp_path, [("nvidia", PCI[0])], probe_of("rocm")).devices[0].reason
    gi.invalidate()
    (tmp_path / "i").mkdir()
    assert "no XPU support" in inventory(tmp_path / "i", [("intel", PCI[0])], probe_of("cpu")).devices[0].reason


def test_igpu_plus_dgpu_amd_only_dgpu_listed_by_torch(tmp_path):
    cards = [("amd", "0000:03:00.0"), ("amd", "0000:0c:00.0")]
    inv = inventory(tmp_path, cards, probe_of("rocm", [tdev(0, "0000:03:00.0", "RX 6700 XT")]))
    d = {x.pci_bus_id: x for x in inv.devices}
    assert d["0000:03:00.0"].usable and d["0000:03:00.0"].name == "RX 6700 XT"
    assert not d["0000:0c:00.0"].usable
    assert "does not list this GPU" in d["0000:0c:00.0"].reason
    assert "HSA_OVERRIDE_GFX_VERSION" in d["0000:0c:00.0"].reason
    assert not gi.needs_choice(inv.devices)


def test_unreadable_dev_nodes_mention_render_group(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    dev = make_dev(tmp_path, mode=0o000)
    inv = inventory(tmp_path, [("amd", PCI[0])], probe_of("rocm", [tdev(0, PCI[0])]), dev_root=dev)
    assert not inv.devices[0].usable
    assert "/dev/kfd" in inv.devices[0].reason and "render" in inv.devices[0].reason


def test_missing_kfd_and_render_nodes(tmp_path):
    (tmp_path / "k").mkdir()
    dev = make_dev(tmp_path / "k", kfd=False)
    inv = inventory(tmp_path / "k", [("amd", PCI[0])], probe_of("rocm", [tdev(0, PCI[0])]), dev_root=dev)
    assert "/dev/kfd is missing" in inv.devices[0].reason
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    gi.invalidate()
    dev2 = make_dev(tmp_path / "x", render=())
    inv = inventory(tmp_path / "y", [("intel", PCI[0])], probe_of("xpu", [tdev(0, PCI[0])]), dev_root=dev2)
    assert "renderD" in inv.devices[0].reason


def test_torch_without_pci_ids_joins_positionally_when_unambiguous(tmp_path):
    t = {"index": 0, "name": "N", "total_memory": None, "pci_bus_id": None}
    inv = inventory(tmp_path, [("nvidia", PCI[0])], probe_of("cuda", [t]))
    assert inv.devices[0].usable and inv.devices[0].torch_index == 0


def test_id_is_stable_across_rescans_and_independent_of_torch(tmp_path):
    a = inventory(tmp_path, [("amd", PCI[0])], probe_of("cuda"))
    gi.invalidate()
    b = gi.get_inventory(sysfs_root=tmp_path / "drm", probe_runner=probe_of("rocm", [tdev(0, PCI[0])]),
                         dev_root=tmp_path / "dev", lspci=NO_LSPCI, env={})
    assert a.devices[0].id == b.devices[0].id == "pci:0000:1a:00.0"


# ---- probe / cache ----------------------------------------------------------

def test_hip_before_cuda_precedence_in_probe_script():
    """Run the real probe script against a fake torch that has both hip and cuda."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d) / "torch"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "__version__='9'\nclass version: hip='7.1'; cuda='13.0'\n"
            "class cuda:\n  device_count=staticmethod(lambda:0)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", gi._PROBE_SCRIPT], capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": d}, timeout=60,
        )
    assert '"build": "rocm"' in proc.stdout


def test_probe_subprocess_real_run_reports_a_result():
    res = gi.run_probe_subprocess(60)
    assert "ok" in res


def test_timeout_is_reported_and_never_cached(tmp_path):
    calls = []

    def runner(t):
        calls.append(t)
        return {"ok": False, "error": gi.PROBE_TIMED_OUT, "timed_out": True}

    for _ in range(2):
        inv = inventory(tmp_path, [("amd", PCI[0])], runner)
    assert len(calls) == 2
    assert gi.PROBE_TIMED_OUT in inv.devices[0].reason
    assert gi.snapshot(sysfs_root=tmp_path / "drm", env={}, lspci=NO_LSPCI) is None


def test_timeout_parameter_is_passed_through(tmp_path):
    seen = []
    inventory(tmp_path, [("amd", PCI[0])], lambda t: seen.append(t) or {"ok": True, "build": "cpu", "devices": []}, timeout_s=3.5)
    assert seen == [3.5]


def test_subprocess_timeout_kills_the_process_group(monkeypatch):
    monkeypatch.setattr(gi, "_PROBE_SCRIPT", "import os,time\nprint(os.getpid(), flush=True)\ntime.sleep(60)")
    res = gi.run_probe_subprocess(1.0)
    assert res == {"ok": False, "error": "probe timed out", "timed_out": True}


def test_pass_fail_cached_then_invalidated(tmp_path):
    n = []

    def runner(t):
        n.append(1)
        return {"ok": False, "error": "ImportError: no torch"}

    inventory(tmp_path, [("amd", PCI[0])], runner)
    inventory(tmp_path, [("amd", PCI[0])], runner)
    assert len(n) == 1
    gi.invalidate()
    inventory(tmp_path, [("amd", PCI[0])], runner)
    assert len(n) == 2


def test_cache_key_changes_with_env(tmp_path):
    n = []

    def runner(t):
        n.append(1)
        return {"ok": True, "build": "cpu", "devices": []}

    sysfs = tmp_path / "drm"
    sysfs.mkdir()
    make_card(sysfs, 0, "amd", PCI[0])
    for env in ({}, {}, {"HSA_OVERRIDE_GFX_VERSION": "10.3.0"}, {"HIP_VISIBLE_DEVICES": "0"}, {"CUDA_VISIBLE_DEVICES": "1"}):
        gi.get_inventory(sysfs_root=sysfs, probe_runner=runner, env=env, lspci=NO_LSPCI)
    assert len(n) == 4


def test_snapshot_is_none_until_cached_and_never_probes(tmp_path):
    sysfs = tmp_path / "drm"
    sysfs.mkdir()
    make_card(sysfs, 0, "nvidia", PCI[0])
    assert gi.snapshot(sysfs_root=sysfs, env={}, lspci=NO_LSPCI) is None
    gi.get_inventory(sysfs_root=sysfs, probe_runner=probe_of("cuda", [tdev(0, PCI[0])]), env={}, lspci=NO_LSPCI,
                     dev_root=make_dev(tmp_path))
    snap = gi.snapshot(sysfs_root=sysfs, env={}, lspci=NO_LSPCI, dev_root=tmp_path / "dev")
    assert snap and snap.devices[0].usable


def test_probe_runner_exception_never_raises(tmp_path):
    def boom(_t):
        raise RuntimeError("x")
    inv = inventory(tmp_path, [("amd", PCI[0])], boom)
    assert not inv.devices[0].usable and "probe failed" in inv.devices[0].reason


# ---- selection + mapper -----------------------------------------------------

def _dev(id, backend, usable=True, index=0):
    return gi.Device(id, "x", "n", backend, index if usable else None, None, usable, None, None)


def test_default_device_preference_and_needs_choice():
    ds = [_dev("a", "xpu"), _dev("b", "rocm"), _dev("c", "cuda"), _dev("d", "cuda", usable=False)]
    assert gi.default_device(ds).id == "c"
    assert gi.default_device(ds[:2]).id == "b"
    assert gi.default_device([_dev("d", "cuda", usable=False)]) is None
    assert gi.needs_choice(ds) and not gi.needs_choice(ds[:1]) and not gi.needs_choice([])


def test_resolve_torch_device():
    ds = [_dev("pci:1", "rocm", index=1), _dev("pci:2", "xpu", index=0), _dev("pci:3", "cuda", index=2)]
    assert gi.resolve_torch_device(None, ds) == "cuda"
    assert gi.resolve_torch_device(None, None) == "cuda"
    assert gi.resolve_torch_device("pci:1", ds) == "cuda:1"
    assert gi.resolve_torch_device("pci:2", ds) == "xpu:0"
    assert gi.resolve_torch_device("pci:3", ds) == "cuda:2"
    assert gi.resolve_torch_device("pci:zzz", ds) == "cuda"
    assert gi.resolve_torch_device("cpu", ds) == "cpu"


# ---- engine_manager / runtime wiring ---------------------------------------

def test_probe_local_torch_new_keys_and_hip_precedence(monkeypatch):
    mod = types.ModuleType("torch")
    mod.__version__ = "2.9"
    mod.version = types.SimpleNamespace(cuda="13.0", hip="7.1")
    mod.cuda = types.SimpleNamespace(is_available=lambda: False, device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "torch", mod)
    info = kokoro_runtime.probe_local_torch()
    assert info["gpu_backend"] == "rocm" and info["hip_version"] == "7.1"
    assert info["torch_cuda_version"] == "13.0" and info["cuda_available"] is False
    mod.version = types.SimpleNamespace(cuda="13.0", hip=None)
    assert kokoro_runtime.probe_local_torch()["gpu_backend"] == "cuda"
    mod.version = types.SimpleNamespace(cuda=None, hip=None)
    assert kokoro_runtime.probe_local_torch()["gpu_backend"] == "cpu"


def _manager(cuda: bool, version="2.14.0"):
    info = {"torch_version": version, "cuda_available": cuda, "error": None if version else "boom"}
    return engine_manager.EngineManager(torch_probe=lambda: info, remote_probe=lambda **k: {})


def test_gpu_availability_unchanged_for_nvidia_and_cpu_only(monkeypatch):
    monkeypatch.setattr(gi, "snapshot", lambda *a, **k: None)
    assert _manager(True).availability("gpu").available is True
    assert _manager(True).availability("gpu").reason is None
    cpu_only = _manager(False).availability("gpu")
    assert (cpu_only.available, cpu_only.reason) == (False, "No CUDA GPU detected")
    broken = _manager(False, version=None).availability("gpu")
    assert broken.reason == "PyTorch unavailable: boom"


def test_gpu_reason_uses_inventory_when_cached(monkeypatch, tmp_path):
    inv = inventory(tmp_path, [("amd", PCI[0])], probe_of("cuda"))
    monkeypatch.setattr(gi, "snapshot", lambda *a, **k: inv)
    reason = _manager(False).availability("gpu").reason
    assert "ROCm torch environment" in reason
    # torch missing keeps the legacy string even with an inventory.
    assert _manager(False, version=None).availability("gpu").reason == "PyTorch unavailable: boom"


def test_gpu_torch_device_null_is_cuda_and_selection_remaps(monkeypatch):
    monkeypatch.setattr(engine_manager, "load_persisted_gpu_device", lambda: None)
    assert engine_manager.gpu_torch_device() == "cuda"
    monkeypatch.setattr(engine_manager, "load_persisted_gpu_device", lambda: "pci:0000:03:00.0")
    inv = gi.Inventory(devices=[_dev("pci:0000:03:00.0", "rocm", index=1)])
    monkeypatch.setattr(gi, "snapshot", lambda *a, **k: inv)
    assert engine_manager.gpu_torch_device() == "cuda:1"
    monkeypatch.setattr(gi, "snapshot", lambda *a, **k: None)
    assert engine_manager.gpu_torch_device() == "cuda"


def test_persisted_gpu_device_roundtrip(tmp_path, monkeypatch):
    import db.database as _db
    monkeypatch.setattr(_db, "engine", _db.create_engine_and_tables(db_url=f"sqlite:///{tmp_path / 'g.db'}"))
    assert engine_manager.load_persisted_gpu_device() is None
    engine_manager.save_persisted_gpu_device("pci:0000:03:00.0")
    assert engine_manager.load_persisted_gpu_device() == "pci:0000:03:00.0"
    engine_manager.save_persisted_gpu_device(None)
    assert engine_manager.load_persisted_gpu_device() is None


def test_persisted_gpu_device_never_raises(monkeypatch):
    import db.database as _db
    monkeypatch.setattr(_db, "engine", None)
    assert engine_manager.load_persisted_gpu_device() is None
    engine_manager.save_persisted_gpu_device("x")


def test_legacy_db_gains_gpu_device_column(tmp_path):
    import sqlite3
    from sqlalchemy import inspect
    import db.database as _db
    f = tmp_path / "l.db"
    c = sqlite3.connect(f)
    c.execute("CREATE TABLE usersettings (id INTEGER PRIMARY KEY)")
    c.commit(); c.close()
    eng = _db.create_engine_and_tables(db_url=f"sqlite:///{f}")
    assert "gpu_device" in {c["name"] for c in inspect(eng).get_columns("usersettings")}
