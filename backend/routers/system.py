"""System capability probe and the runtime engine switch.

Reports what the backend can actually do right now — the device Kokoro was
built on, what torch sees, and whether the remote Modal backend is configured
and reachable — instead of leaving the frontend to guess (or to invent a
"Needs CUDA" pill with nothing behind it).

``/api/system/engine`` is the same idea one level up: it lists the engines with
the reason each one is or is not usable, and switches between them without a
restart. Switching is genuinely slow (a local model load, or a Modal container
boot), so it runs on a worker thread and a second ``GET`` reports progress.
"""
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import logging

import storage
from services import engine_manager, gpu_inventory, kokoro_runtime, render_pause, render_triggers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/capabilities")
def get_capabilities() -> dict[str, Any]:
    """Live probe of local device, torch build and remote backend reachability."""
    return kokoro_runtime.capabilities()


class SwitchRequest(BaseModel):
    engine: str


class RenderPauseRequest(BaseModel):
    paused: bool


@router.get("/render-pause")
def get_render_pause() -> dict[str, bool]:
    """Whether background rendering is paused so the reader has the machine."""
    return {"paused": render_pause.is_paused()}


@router.post("/render-pause")
def set_render_pause(body: RenderPauseRequest) -> dict[str, bool]:
    return {"paused": render_pause.set_paused(body.paused)}


@router.get("/engine")
def get_engine() -> dict[str, Any]:
    """Which engine is live, which one the user picked, and what is available."""
    return engine_manager.manager.state()


@router.post("/engine")
async def set_engine(body: SwitchRequest) -> dict[str, Any]:
    """Save the engine choice. The app must restart to load it.

    The engine is no longer swapped live: a live swap caused stale playback and
    render bugs, and a restart loads a known state. 400 for an unknown id, 409 when
    the probe says this machine cannot run it (with the probe's reason).
    """
    try:
        await run_in_threadpool(engine_manager.manager.request_switch, body.engine)
    except engine_manager.UnknownEngine as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except engine_manager.EngineUnavailable as exc:
        raise HTTPException(status_code=409, detail=exc.reason)
    return engine_manager.manager.state()


# --------------------------------------------------------------------------
# Kokoro ONNX model install (like Piper voices, the files are fetched on demand)

_install_lock = threading.Lock()
_install_state: dict[str, Any] = {"status": "idle", "file": None, "downloaded": 0,
                                  "total": 0, "error": None}


def _run_kokoro_onnx_install() -> None:
    from services import kokoro_onnx_engine

    def progress(name: str, done: int, total: int) -> None:
        _install_state.update(file=name, downloaded=done, total=total)

    try:
        kokoro_onnx_engine.install(storage.KOKORO_ONNX_DIR, progress=progress)
        _install_state.update(status="done", error=None)
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        logger.exception("[kokoro-onnx] model download failed")
        _install_state.update(status="error", error=f"{type(exc).__name__}: {exc}")


@router.get("/engine/kokoro-onnx/install")
def get_kokoro_onnx_install() -> dict[str, Any]:
    """Progress of the model download (``idle|running|done|error``)."""
    from services import kokoro_onnx_engine

    state = dict(_install_state)
    if state["status"] == "idle" and kokoro_onnx_engine.is_installed(storage.KOKORO_ONNX_DIR):
        state["status"] = "done"
    return state


@router.post("/engine/kokoro-onnx/install", status_code=202)
def install_kokoro_onnx() -> dict[str, Any]:
    """Start downloading the ~325 MB Kokoro ONNX model in the background (idempotent)."""
    from services import kokoro_onnx_engine

    with _install_lock:
        if _install_state["status"] == "running":
            return dict(_install_state)
        if kokoro_onnx_engine.is_installed(storage.KOKORO_ONNX_DIR):
            _install_state.update(status="done", error=None)
            return dict(_install_state)
        _install_state.update(status="running", file=None, downloaded=0, total=0, error=None)
    threading.Thread(target=_run_kokoro_onnx_install, name="kokoro-onnx-install",
                     daemon=True).start()
    return dict(_install_state)


# --------------------------------------------------------------------------
# GPU inventory
#
# The torch probe is a subprocess that can take up to 15 s (and a wedged driver
# can eat all of it), so no request ever runs it. A request reads the cache; when
# there is none it starts ONE background probe and answers "validating" with the
# sysfs-only hardware list, and the client polls. torch is never imported here.
# --------------------------------------------------------------------------

#: After a timed-out probe, a plain GET will not start another one for this long.
GPU_TIMEOUT_RETRY_S = 30.0
#: An explicit Re-scan is allowed sooner, but not instantly after a timeout.
GPU_REFRESH_MIN_S = 5.0

_gpu_lock = threading.Lock()
_gpu_probing = False
_gpu_timed_out_at: float | None = None
#: pci bus id -> product name, filled by the background thread (lspci can block).
_gpu_names: dict[str, str] = {}


def _name_lookup(pci: str | None) -> str | None:
    return _gpu_names.get(pci) if pci else None


def _gpu_probe_worker() -> None:
    global _gpu_probing, _gpu_timed_out_at
    try:
        try:
            for h in gpu_inventory.scan_hardware():  # real lspci; fine off-request
                if h.get("pci_bus_id") and h.get("name"):
                    _gpu_names[h["pci_bus_id"]] = h["name"]
        except Exception:
            logger.debug("[gpu] hardware scan failed", exc_info=True)
        inv = gpu_inventory.get_inventory(True)
        with _gpu_lock:
            _gpu_timed_out_at = (
                time.monotonic() if inv.probe_error == gpu_inventory.PROBE_TIMED_OUT else None
            )
    except Exception:
        logger.warning("[gpu] background probe crashed", exc_info=True)
    finally:
        with _gpu_lock:
            _gpu_probing = False


def _start_gpu_probe(min_interval_s: float) -> bool:
    """Start the background probe unless one is running or a timeout was too recent."""
    global _gpu_probing
    with _gpu_lock:
        if _gpu_probing:
            return False
        if _gpu_timed_out_at is not None and time.monotonic() - _gpu_timed_out_at < min_interval_s:
            return False
        _gpu_probing = True
    try:
        threading.Thread(target=_gpu_probe_worker, name="gpu-probe", daemon=True).start()
    except Exception:
        with _gpu_lock:
            _gpu_probing = False
        raise
    return True


def _device_json(d: gpu_inventory.Device) -> dict[str, Any]:
    return {
        "id": d.id,
        "vendor": d.vendor,
        "vendor_label": gpu_inventory.VENDOR_LABEL.get(d.vendor, d.vendor),
        "name": d.name,
        "backend": d.backend,
        "usable": d.usable,
        "reason": d.reason,
        "vram_mb": d.vram_mb,
    }


def _hardware_json(hw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Layer A only: hardware is known, usability is not yet."""
    out = []
    for h in hw:
        vendor = h["vendor"]
        out.append({
            "id": gpu_inventory._device_id(h),
            "vendor": vendor,
            "vendor_label": gpu_inventory.VENDOR_LABEL[vendor],
            "name": h.get("name") or f"{gpu_inventory.VENDOR_LABEL[vendor]} GPU",
            "backend": gpu_inventory.VENDOR_BACKEND[vendor],
            "usable": None,
            "reason": "validating",
            "vram_mb": h.get("vram_mb"),
        })
    return out


def _gpu_state(*, start: bool = True, min_interval_s: float = GPU_TIMEOUT_RETRY_S) -> dict[str, Any]:
    """The response body. Never blocks, never touches torch."""
    selected = engine_manager.load_persisted_gpu_device()
    body: dict[str, Any] = {
        "devices": [], "selected": selected, "default": None,
        "needs_choice": False, "torch_build": None, "state": "ready",
    }
    hw = gpu_inventory.scan_hardware(lspci=_name_lookup)
    if not hw:
        return body
    inv = gpu_inventory.snapshot(lspci=_name_lookup)
    if inv is not None:
        body["devices"] = [_device_json(d) for d in inv.devices]
        body["torch_build"] = inv.torch_build
        chosen = gpu_inventory.default_device(inv.devices)
        body["default"] = chosen.id if chosen else None
        body["needs_choice"] = gpu_inventory.needs_choice(inv.devices)
        if inv.probe_error:
            body["state"] = "error"
        return body
    with _gpu_lock:
        timed_out_at = _gpu_timed_out_at
        probing = _gpu_probing
    if start and not probing:
        _start_gpu_probe(min_interval_s)
        with _gpu_lock:
            timed_out_at = _gpu_timed_out_at
            probing = _gpu_probing
    if not probing and timed_out_at is not None:
        joined = gpu_inventory.join(hw, {"ok": False, "error": gpu_inventory.PROBE_TIMED_OUT})
        body["devices"] = [_device_json(d) for d in joined.devices]
        body["state"] = "timed_out"
        return body
    body["devices"] = _hardware_json(hw)
    body["state"] = "validating"
    return body


@router.get("/gpus")
def get_gpus() -> dict[str, Any]:
    """Detected GPUs and whether PyTorch can drive each. Poll while ``validating``."""
    return _gpu_state()


@router.post("/gpus/refresh")
def refresh_gpus() -> dict[str, Any]:
    """Forget the cached inventory and re-probe in the background."""
    gpu_inventory.invalidate()
    return _gpu_state(min_interval_s=GPU_REFRESH_MIN_S)


class GpuSelectRequest(BaseModel):
    device_id: str | None = None


@router.put("/gpus")
def select_gpu(body: GpuSelectRequest) -> dict[str, Any]:
    """Save which GPU the ``gpu`` engine should use (``null`` = legacy default).

    The engine only loads at startup, so this never touches the live engine; the
    reply says a restart is required. 400 for an unknown id, 409 when the device
    is known but cannot be used (with the device's own reason).
    """
    if body.device_id is not None:
        state = _gpu_state()
        device = next((d for d in state["devices"] if d["id"] == body.device_id), None)
        if device is None:
            raise HTTPException(status_code=400, detail=f"Unknown GPU id {body.device_id!r}.")
        if device["usable"] is not True:
            reason = device["reason"]
            if device["usable"] is None:
                reason = "GPU validation is still running; try again in a few seconds."
            raise HTTPException(status_code=409, detail=reason or "This GPU cannot be used.")
    engine_manager.save_persisted_gpu_device(body.device_id)
    if engine_manager.load_persisted_gpu_device() != body.device_id:
        raise HTTPException(status_code=500, detail="Could not save the GPU choice.")
    result = _gpu_state(start=False)
    result["restart_required"] = True
    return result
