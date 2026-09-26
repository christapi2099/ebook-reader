"""Startup state and live capability probing for the Kokoro backend.

``main._init_kokoro()`` records *what actually happened* at startup here, and
``GET /api/system/capabilities`` reads it back out alongside freshly probed
torch/CUDA facts.  Keeping the state in its own module (rather than on ``main``)
is what lets a router import it without a circular import.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from services.modal_remote import (
    RemoteConfig,
    modal_credentials_present,
    probe as probe_remote,
    resolve_transport,
)
from services.tts_engine import SAMPLE_RATE

logger = logging.getLogger(__name__)

VALID_BACKENDS = ("local", "remote", "auto")
DEFAULT_BACKEND = "local"


@dataclass
class KokoroRuntime:
    """What the process ended up doing, as opposed to what it was asked to do."""

    requested_backend: str = DEFAULT_BACKEND
    active_backend: str = "none"  # "local" | "remote" | "none"
    device: str | None = None  # device the local pipeline was built on
    model_repo: str | None = None
    error: str | None = None  # why active_backend is "none", if it is
    remote_error: str | None = None
    initialized_at: float | None = None

    def reset(self) -> None:
        self.active_backend = "none"
        self.device = None
        self.model_repo = None
        self.error = None
        self.remote_error = None
        self.initialized_at = None

    def record_local(self, device: str, model_repo: str) -> None:
        self.active_backend = "local"
        self.device = device
        self.model_repo = model_repo
        self.error = None
        # `remote_error` is deliberately kept: when the process fell back to
        # local, that string is the only explanation of why remote is not in use.
        self.initialized_at = time.time()

    def record_remote(self) -> None:
        self.active_backend = "remote"
        # A remote run means no local device is in use, and any earlier local
        # failure is no longer the reason the reader is silent.
        self.device = None
        self.error = None
        self.remote_error = None
        self.initialized_at = time.time()

    def record_failure(self, error: str) -> None:
        self.active_backend = "none"
        self.error = error
        self.initialized_at = time.time()


runtime = KokoroRuntime()


def normalize_backend(value: str | None) -> str:
    """Map ``KOKORO_BACKEND`` onto a supported value, defaulting to local."""
    backend = (value or DEFAULT_BACKEND).strip().lower()
    if backend not in VALID_BACKENDS:
        logger.warning("Unknown KOKORO_BACKEND=%r, using %r", value, DEFAULT_BACKEND)
        return DEFAULT_BACKEND
    return backend


def probe_local_torch() -> dict[str, Any]:
    """Ask torch, right now, what it can see.

    This is deliberately a live probe: the sandbox that runs the tests hides
    ``/dev/nvidia*``, so a cached startup answer would be able to disagree with
    reality on a machine where the GPU is genuinely present.
    """
    info: dict[str, Any] = {
        "torch_version": None,
        "torch_cuda_version": None,
        "cuda_available": False,
        "cuda_device_count": 0,
        "gpu_name": None,
        "error": None,
    }
    try:
        import torch
    except Exception as exc:  # torch missing or broken: report, never raise
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info

    info["torch_version"] = getattr(torch, "__version__", None)
    info["torch_cuda_version"] = getattr(getattr(torch, "version", None), "cuda", None)
    try:
        available = bool(torch.cuda.is_available())
        info["cuda_available"] = available
        if available:
            info["cuda_device_count"] = int(torch.cuda.device_count())
            info["gpu_name"] = torch.cuda.get_device_name(0)
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def capabilities(
    env: Mapping[str, str] | None = None,
    checker: Callable[[], None] | None = None,
    state: KokoroRuntime | None = None,
) -> dict[str, Any]:
    """Assemble the ``/api/system/capabilities`` payload.

    ``checker`` is forwarded to the remote probe so tests can answer
    "is Modal reachable" without a network.
    """
    env = os.environ if env is None else env
    state = state or runtime
    config = RemoteConfig.from_env(env)

    local = probe_local_torch()
    local["device_in_use"] = state.device
    local["model_repo"] = state.model_repo

    remote = probe_remote(config=config, env=env, checker=checker)
    # Only assert what the probe observed; `configured` with no credentials is
    # a configuration fact, not a reachability claim.
    remote["credentials_present"] = modal_credentials_present(env)
    remote["would_use"] = resolve_transport(config, env)

    return {
        "active_backend": state.active_backend,
        "requested_backend": state.requested_backend,
        "synthesis_available": state.active_backend != "none",
        "sample_rate": SAMPLE_RATE,
        "local": local,
        "remote": remote,
        "errors": {
            "startup": state.error,
            "remote": state.remote_error,
        },
    }
