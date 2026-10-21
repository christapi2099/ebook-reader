"""GPU inventory: what hardware is here, and which of it PyTorch can drive.

Two layers, joined into :class:`Device` records:

* Layer A (hardware) reads ``/sys/class/drm`` only. It never imports torch.
* Layer B (torch) runs in a cached SUBPROCESS so ``torch.cuda`` is never
  initialised inside the API process (a wedged ROCm/CUDA init must not take the
  server down, and a timed-out probe can be killed as a process group).

Nothing here raises, and nothing here runs at import time. Callers that must
not block (``/api/system/capabilities``) use :func:`snapshot`, which only ever
returns a cached answer.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

SYSFS_DRM = Path("/sys/class/drm")
DEV_ROOT = Path("/dev")
PROBE_TIMEOUT_S = 15.0
PROBE_TIMED_OUT = "probe timed out"

VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
VENDOR_LABEL = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}
#: The torch build kind that can drive each vendor's hardware.
VENDOR_BACKEND = {"nvidia": "cuda", "amd": "rocm", "intel": "xpu"}
_PREFERENCE = ("cuda", "rocm", "xpu")
#: Kernel drivers that expose a DRM card with no real GPU behind it.
_VIRTUAL_DRIVERS = {"vkms", "simpledrm", "simple-framebuffer", "vgem", "udl", "evdi"}

_CARD_RE = re.compile(r"card\d+")
_PCI_RE = re.compile(r"^[0-9a-fA-F]{4,8}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")


@dataclass(frozen=True)
class Device:
    id: str
    vendor: str
    name: str
    backend: str
    torch_index: int | None
    pci_bus_id: str | None
    usable: bool
    reason: str | None
    vram_mb: int | None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Inventory:
    devices: list[Device] = field(default_factory=list)
    torch_build: str | None = None
    torch_version: str | None = None
    probe_error: str | None = None


# --------------------------------------------------------------------------
# Layer A: sysfs
# --------------------------------------------------------------------------


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace").strip()
    except Exception:
        return None


def normalize_pci(value: str | None) -> str | None:
    """``0000:1A:00.0`` / ``00000000:1a:00.0`` -> ``0000:1a:00.0``; else ``None``."""
    if not value:
        return None
    value = value.strip()
    if not _PCI_RE.match(value):
        return None
    domain, bus, rest = value.lower().split(":")
    return f"{domain[-4:].zfill(4)}:{bus}:{rest}"


def _uevent(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (_read(path) or "").splitlines():
        key, _, val = line.partition("=")
        if key:
            out[key] = val
    return out


def _lspci_name(pci: str | None) -> str | None:
    if not pci or not shutil.which("lspci"):
        return None
    try:
        proc = subprocess.run(
            ["lspci", "-nn", "-s", pci], capture_output=True, text=True, timeout=3
        )
        line = proc.stdout.strip().splitlines()[0]
    except Exception:
        return None
    # "03:00.0 VGA compatible controller [0300]: Advanced Micro Devices ... [1002:73df] (rev c1)"
    _, _, tail = line.partition(": ")
    tail = re.sub(r"\s*\(rev [0-9a-f]+\)\s*$", "", tail)
    return tail or None


def scan_hardware(
    sysfs_root: Path | str = SYSFS_DRM,
    lspci: Callable[[str | None], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Display-class GPUs from sysfs. Never raises."""
    lookup = _lspci_name if lspci is None else lspci
    root = Path(sysfs_root)
    found: list[dict[str, Any]] = []
    try:
        entries = sorted(p for p in root.iterdir() if _CARD_RE.fullmatch(p.name))
    except Exception:
        return found
    for card in entries:
        try:
            dev = card / "device"
            vendor = VENDORS.get((_read(dev / "vendor") or "").lower())
            cls = (_read(dev / "class") or "").lower()
            if vendor is None or not cls.startswith("0x03"):
                continue
            uevent = _uevent(dev / "uevent")
            if uevent.get("DRIVER", "").lower() in _VIRTUAL_DRIVERS:
                continue
            pci = normalize_pci(Path(os.path.realpath(dev)).name)
            name = _read(dev / "product_name") or lookup(pci)
            vram = _read(dev / "mem_info_vram_total")
            renders = sorted(p.name for p in (dev / "drm").glob("renderD*")) if (dev / "drm").is_dir() else []
            found.append({
                "card": card.name,
                "vendor": vendor,
                "pci_bus_id": pci,
                "name": name,
                "vram_mb": int(vram) // (1024 * 1024) if vram and vram.isdigit() else None,
                "render_nodes": renders,
                "vendor_hex": (_read(dev / "vendor") or "").lower(),
                "device_hex": (_read(dev / "device") or "").lower(),
            })
        except Exception:
            logger.debug("[gpu] could not read %s", card, exc_info=True)
    return found


def check_access(vendor: str, render_nodes: list[str], dev_root: Path | str = DEV_ROOT) -> str | None:
    """``None`` when the current user can open the device nodes, else the reason."""
    root = Path(dev_root)
    fix = "Add your user to the 'render' and 'video' groups (then log in again)."
    try:
        if vendor == "amd":
            kfd = root / "kfd"
            if not kfd.exists():
                return (
                    "/dev/kfd is missing: the amdgpu/ROCm kernel driver is not loaded, "
                    "or the container needs --device=/dev/kfd --device=/dev/dri."
                )
            if not os.access(kfd, os.R_OK | os.W_OK):
                return f"/dev/kfd is not readable/writable by the current user. {fix}"
        if vendor in ("amd", "intel"):
            nodes = [root / "dri" / n for n in render_nodes] or sorted((root / "dri").glob("renderD*"))
            if not nodes:
                return "No /dev/dri/renderD* node exists (driver not loaded, or the container needs --device=/dev/dri)."
            if not any(os.access(n, os.R_OK | os.W_OK) for n in nodes):
                return f"/dev/dri/renderD* is not readable/writable by the current user. {fix}"
    except Exception:
        logger.debug("[gpu] access check failed", exc_info=True)
    return None


# --------------------------------------------------------------------------
# Layer B: torch, in a subprocess
# --------------------------------------------------------------------------

_PROBE_SCRIPT = r"""
import json
out = {"ok": True, "build": "cpu", "torch_version": None, "devices": []}
try:
    import torch
    out["torch_version"] = getattr(torch, "__version__", None)
    v = getattr(torch, "version", None)
    xpu = getattr(torch, "xpu", None)
    if getattr(v, "hip", None):
        out["build"] = "rocm"
    elif getattr(v, "cuda", None):
        out["build"] = "cuda"
    else:
        try:
            if xpu is not None and xpu.is_available():
                out["build"] = "xpu"
        except Exception:
            pass
    mod = xpu if out["build"] == "xpu" else torch.cuda
    if out["build"] != "cpu":
        for i in range(int(mod.device_count())):
            d = {"index": i, "name": None, "total_memory": None, "pci_bus_id": None}
            try:
                d["name"] = mod.get_device_name(i)
                p = mod.get_device_properties(i)
                d["total_memory"] = int(getattr(p, "total_memory", 0)) or None
                pci = getattr(p, "pci_bus_id", None)
                if isinstance(pci, str):
                    d["pci_bus_id"] = pci
                elif pci is not None and hasattr(p, "pci_domain_id") and hasattr(p, "pci_device_id"):
                    d["pci_bus_id"] = "%04x:%02x:%02x.0" % (p.pci_domain_id, pci, p.pci_device_id)
            except Exception:
                pass
            out["devices"].append(d)
except Exception as exc:
    out = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
print("__GPU_PROBE__" + json.dumps(out))
"""


def run_probe_subprocess(timeout_s: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    """Run the torch probe in a child process; kill its process group on timeout."""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _PROBE_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
    except Exception as exc:
        return {"ok": False, "error": f"could not start the probe: {exc}"}
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return {"ok": False, "error": PROBE_TIMED_OUT, "timed_out": True}
    except Exception as exc:
        return {"ok": False, "error": f"probe failed: {exc}"}
    for line in reversed(stdout.splitlines()):
        if line.startswith("__GPU_PROBE__"):
            try:
                return json.loads(line[len("__GPU_PROBE__"):])
            except Exception:
                break
    tail = (stderr or "").strip().splitlines()[-1:] or [f"exit code {proc.returncode}"]
    return {"ok": False, "error": f"probe produced no result: {tail[0]}"}


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

_lock = threading.Lock()
_cache: dict[tuple, dict[str, Any]] = {}


def _torch_version_cheap() -> str | None:
    try:
        from importlib.metadata import version

        return version("torch")
    except Exception:
        return None


def cache_key(env: Mapping[str, str] | None = None) -> tuple:
    env = os.environ if env is None else env
    return (
        _torch_version_cheap(),
        env.get("HSA_OVERRIDE_GFX_VERSION"),
        env.get("CUDA_VISIBLE_DEVICES"),
        env.get("HIP_VISIBLE_DEVICES"),
    )


def invalidate() -> None:
    with _lock:
        _cache.clear()


def _probe(
    timeout_s: float,
    runner: Callable[[float], dict[str, Any]] | None,
    env: Mapping[str, str] | None,
    refresh: bool,
) -> dict[str, Any]:
    key = cache_key(env)
    if not refresh:
        with _lock:
            hit = _cache.get(key)
        if hit is not None:
            return hit
    try:
        result = (runner or run_probe_subprocess)(timeout_s)
        if not isinstance(result, dict):
            result = {"ok": False, "error": "probe returned an invalid result"}
    except Exception as exc:
        result = {"ok": False, "error": f"probe failed: {exc}"}
    if not result.get("timed_out"):
        with _lock:
            _cache[key] = result
    return result


# --------------------------------------------------------------------------
# Join
# --------------------------------------------------------------------------


def _mismatch_reason(vendor: str, build: str, torch_version: str | None) -> str:
    label = VENDOR_LABEL[vendor]
    ver = f" (torch {torch_version})" if torch_version else ""
    if vendor == "amd":
        return (
            f"AMD GPU detected, but the installed torch is a {build.upper()} build{ver}. "
            "An AMD GPU needs the ROCm torch environment "
            "(run scripts/setup-rocm-env.sh, see docs/AMD_ROCM.md)."
        )
    if vendor == "nvidia":
        return (
            f"NVIDIA GPU detected, but the installed torch is a {build.upper()} build{ver}. "
            "An NVIDIA GPU needs a CUDA build of torch."
        )
    return (
        f"Intel GPU detected, but the installed torch has no XPU support "
        f"({build.upper()} build{ver}). An Intel GPU needs an XPU build of torch."
    )


def _match_torch_devices(hw: list[dict[str, Any]], tdevs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map sysfs card name -> torch device, joining on PCI bus id."""
    by_pci = {normalize_pci(t.get("pci_bus_id")): t for t in tdevs if normalize_pci(t.get("pci_bus_id"))}
    matched: dict[str, dict[str, Any]] = {}
    for h in hw:
        t = by_pci.get(h["pci_bus_id"])
        if t is not None:
            matched[h["card"]] = t
    # Torch without pci_bus_id: only join positionally when it is unambiguous.
    if not by_pci and len(tdevs) == len(hw):
        ordered = sorted(hw, key=lambda h: h["pci_bus_id"] or h["card"])
        for h, t in zip(ordered, sorted(tdevs, key=lambda t: t.get("index", 0))):
            matched[h["card"]] = t
    return matched


def _device_id(h: dict[str, Any]) -> str:
    return f"pci:{h['pci_bus_id']}" if h["pci_bus_id"] else f"drm:{h['card']}"


def join(hw: list[dict[str, Any]], probe: dict[str, Any] | None, dev_root: Path | str = DEV_ROOT) -> Inventory:
    ok = bool(probe and probe.get("ok"))
    build = probe.get("build") if ok else None
    tver = probe.get("torch_version") if probe else None
    error = None if ok else ((probe or {}).get("error") or "torch probe unavailable")
    matched = _match_torch_devices(hw, probe.get("devices") or []) if ok else {}
    devices: list[Device] = []
    for h in hw:
        vendor = h["vendor"]
        native = VENDOR_BACKEND[vendor]
        t = matched.get(h["card"])
        usable, reason = False, None
        if not ok:
            reason = f"PyTorch probe failed: {error}"
        elif build != native:
            reason = _mismatch_reason(vendor, build or "cpu", tver)
        else:
            reason = check_access(vendor, h["render_nodes"], dev_root)
            if reason is None and t is None:
                reason = f"torch ({build} build) does not list this GPU."
                if vendor == "amd":
                    reason += " If it is an officially unsupported chip, set HSA_OVERRIDE_GFX_VERSION."
            usable = reason is None
        vram = h["vram_mb"]
        if t and t.get("total_memory"):
            vram = int(t["total_memory"]) // (1024 * 1024)
        fallback = f"{VENDOR_LABEL[vendor]} GPU [{h['vendor_hex'][2:]}:{h['device_hex'][2:]}]"
        devices.append(Device(
            id=_device_id(h),
            vendor=vendor,
            name=(t or {}).get("name") or h["name"] or fallback,
            backend=native,
            torch_index=int(t["index"]) if t and usable else None,
            pci_bus_id=h["pci_bus_id"],
            usable=usable,
            reason=reason,
            vram_mb=vram,
        ))
    return Inventory(devices=devices, torch_build=build, torch_version=tver, probe_error=error)


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------


def get_inventory(
    refresh: bool = False,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    probe_runner: Callable[[float], dict[str, Any]] | None = None,
    sysfs_root: Path | str = SYSFS_DRM,
    dev_root: Path | str = DEV_ROOT,
    env: Mapping[str, str] | None = None,
    lspci: Callable[[str | None], str | None] | None = None,
) -> Inventory:
    """Full inventory; runs (and caches) the subprocess probe. May block up to ``timeout_s``."""
    try:
        hw = scan_hardware(sysfs_root, lspci)
        if not hw:
            return Inventory()
        return join(hw, _probe(timeout_s, probe_runner, env, refresh), dev_root)
    except Exception as exc:
        logger.debug("[gpu] inventory failed", exc_info=True)
        return Inventory(probe_error=f"{type(exc).__name__}: {exc}")


def snapshot(
    refresh: bool = False,
    *,
    sysfs_root: Path | str = SYSFS_DRM,
    dev_root: Path | str = DEV_ROOT,
    env: Mapping[str, str] | None = None,
    lspci: Callable[[str | None], str | None] | None = None,
    **kwargs: Any,
) -> Inventory | None:
    """The cached inventory, or ``None`` when no probe has been cached. Never blocks
    unless ``refresh`` is true."""
    if refresh:
        return get_inventory(True, sysfs_root=sysfs_root, dev_root=dev_root, env=env, lspci=lspci, **kwargs)
    try:
        with _lock:
            hit = _cache.get(cache_key(env))
        if hit is None:
            return None
        return join(scan_hardware(sysfs_root, lspci), hit, dev_root)
    except Exception:
        return None


def default_device(devices: list[Device]) -> Device | None:
    usable = [d for d in devices if d.usable]
    for backend in _PREFERENCE:
        for d in usable:
            if d.backend == backend:
                return d
    return None


def needs_choice(devices: list[Device]) -> bool:
    return sum(1 for d in devices if d.usable) > 1


def resolve_torch_device(device_id: str | None, devices: list[Device] | None) -> str:
    """THE mapper from a persisted device id to a torch device string.

    ``None`` is the legacy behaviour (``"cuda"``, exactly as before). An id that
    is unknown or has no torch index also falls back to ``"cuda"`` so a stale
    selection fails the way the legacy path would, rather than inventing a device.
    """
    if device_id is None:
        return "cuda"
    if device_id == "cpu":
        return "cpu"
    for d in devices or []:
        if d.id == device_id and d.torch_index is not None:
            if d.backend in ("cuda", "rocm"):
                return f"cuda:{d.torch_index}"
            if d.backend == "xpu":
                return f"xpu:{d.torch_index}"
    logger.warning("[gpu] unknown or unusable device %r; using the legacy device", device_id)
    return "cuda"


def unavailable_reason(inv: Inventory | None) -> str | None:
    """Actionable text for "torch cannot see a GPU", or ``None`` to keep the legacy string."""
    if inv is None or not inv.devices:
        return None
    reasons = [d.reason for d in inv.devices if not d.usable and d.reason]
    return " ".join(dict.fromkeys(reasons)) or None
