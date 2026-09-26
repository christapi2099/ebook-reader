"""Single owner of "which Kokoro object is live".

Before this module the choice was made once, inside ``main._init_kokoro()``, and
the only way to change it was to edit an environment variable and restart the
process. ``EngineManager`` makes the same decision at runtime: it builds the
requested engine first and swaps it in only on success, so a failed switch
leaves the previous engine running and the reader keeps working.

Three engines, all funnelling into the same ``TTSEngine`` contract:

``cpu`` / ``gpu``
    The in-process ``KPipeline``. ``gpu`` asks torch for CUDA; switching between
    the two drops the old pipeline and empties torch's cache first, because a
    4 GB card cannot hold two checkpoints.
``modal``
    ``ModalKokoroClient``, which behaves like ``KPipeline`` (same call, real word
    timestamps) but runs on a Modal GPU.

The manager owns no FastAPI or router state. When the live engine changes it
calls whatever applier was registered with :func:`register_applier`; ``main``
registers one that forwards to ``set_kokoro`` on the three routers. That keeps
the module independently testable with fake builders.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from services import env_config, kokoro_runtime, modal_remote

logger = logging.getLogger(__name__)

KOKORO_MODEL_REPO = "hexgrad/Kokoro-82M"
KOKORO_LANG_CODE = "a"

CPU = "cpu"
GPU = "gpu"
MODAL = "modal"
ENGINE_IDS = (CPU, GPU, MODAL)

ENGINE_LABELS = {
    CPU: "This device · CPU",
    GPU: "This device · GPU",
    MODAL: "Cloud · Modal GPU",
}

# One vocabulary for "what is the GPU doing", shared by this module, the export
# status endpoint and the reader WebSocket, so the UI can render one indicator.
PHASE_IDLE = "idle"
PHASE_UPLOADING = "uploading"
PHASE_STARTING = "starting"
PHASE_WARMING_UP = "warming_up"
PHASE_PROCESSING = "processing"
PHASE_ENCODING = "encoding"
PHASE_READY = "ready"
PHASE_COMPLETE = "complete"
PHASE_ERROR = "error"

# How a failure reads in the UI. The handoff's rule is that error copy names the
# failing stage and the reason; the phase is the part a caller cannot infer from
# the exception text, so it is prefixed here in one place.
PHASE_FAILURE_LABELS = {
    PHASE_UPLOADING: "sending text to Modal",
    PHASE_STARTING: "starting the GPU",
    PHASE_WARMING_UP: "warming up the GPU",
    PHASE_PROCESSING: "synthesizing audio",
    PHASE_ENCODING: "encoding the file",
}


def phase_failure_message(phase: str | None, reason: object) -> str:
    """``"Failed while warming up the GPU · <reason>"`` (or just the reason)."""
    label = PHASE_FAILURE_LABELS.get(phase or "")
    if not label:
        return str(reason)
    return f"Failed while {label} · {reason}"

# How long the background warm-up watcher keeps reporting before giving up and
# marking the engine ready anyway (a warm-up that never returns must not leave
# the UI spinning for ever). Read through env_config, not float(): this runs at
# import time, so an unparsable value used to stop the app from importing at all.
# Zero is treated as a typo — a zero window would mark the engine ready
# immediately, which is the one outcome this bound exists to prevent.
DEFAULT_WARMUP_WATCH_SECONDS = 180.0
WARMUP_WATCH_SECONDS = env_config.positive_seconds(
    "KOKORO_WARMUP_WATCH_SECONDS", DEFAULT_WARMUP_WATCH_SECONDS
)
WARMUP_POLL_SECONDS = 1.0

# Reachability budget for GET /api/system/engine, which the Settings panel polls.
OPTIONS_PROBE_SECONDS = 3.0


class UnknownEngine(ValueError):
    """The requested engine id is not one of ``ENGINE_IDS``."""


class EngineUnavailable(RuntimeError):
    """The probe says this engine cannot run on this machine."""

    def __init__(self, engine_id: str, reason: str) -> None:
        self.engine_id = engine_id
        self.reason = reason
        super().__init__(reason)


class EngineBuildError(RuntimeError):
    """The probe said yes but building the engine failed; the old one stays live."""


@dataclass(frozen=True)
class EngineOption:
    """One entry of the selector, as the UI needs it."""

    id: str
    label: str
    available: bool
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "available": self.available,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------
# Builders (injectable so tests need no torch, no network)
# --------------------------------------------------------------------------


def build_local(device: str) -> tuple[Any | None, str | None]:
    """Build an in-process ``KPipeline`` on an explicit device.

    Returns ``(pipeline, error)``. The caller decides the device; this function
    never guesses, so ``cpu`` and ``gpu`` are genuinely different requests
    instead of "cuda if it happens to be there".
    """
    try:
        import torch  # noqa: F401  (imported for its side effect on the device)
        from kokoro import KPipeline

        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        logger.info("[kokoro] initializing local pipeline on %s", device)
        pipeline = KPipeline(
            lang_code=KOKORO_LANG_CODE,
            repo_id=KOKORO_MODEL_REPO,
            device=device,
        )
    except Exception as exc:
        # Broad on purpose (a broken CUDA install raises almost anything), but
        # loud: the traceback is the point, and the reason is returned so the
        # capability endpoint can explain a bare `null`.
        logger.exception("[kokoro] local pipeline failed to initialise")
        return None, f"{type(exc).__name__}: {exc}"
    logger.info("[kokoro] local pipeline ready on %s", device)
    return pipeline, None


def build_remote(require_reachable: bool, probe_timeout_s: float | None = None) -> tuple[Any | None, str | None]:
    """Build the Modal client, or explain why it could not be built.

    ``require_reachable`` is what separates an explicit "use Modal" request from
    ``auto``: an explicit request is allowed to build a client against an app
    that is not answering yet (the first call will say so, loudly), while
    ``auto`` refuses to pay a cold start it cannot justify.
    """
    config = modal_remote.RemoteConfig.from_env()
    transport = modal_remote.resolve_transport(config)
    if transport is None:
        return None, (
            "no Modal credentials (MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or ~/.modal.toml) "
            "and no MODAL_KOKORO_HEALTH_URL"
        )
    if require_reachable:
        status = modal_remote.probe(config=config, timeout_s=probe_timeout_s)
        if not status["reachable"]:
            return None, f"remote backend not reachable: {status['error']}"
    return modal_remote.ModalKokoroClient(config=config), None


# --------------------------------------------------------------------------
# Persistence of the chosen engine
# --------------------------------------------------------------------------


def load_persisted_engine() -> str | None:
    """The engine the user last selected, or ``None``.

    Failures are swallowed on purpose: a database that cannot be read must not
    stop the app from starting with a sensible default.
    """
    try:
        import db.database as _db
        from db.models import UserSettings
        from sqlmodel import Session

        with Session(_db.engine) as session:
            settings = session.get(UserSettings, 1)
            value = getattr(settings, "tts_engine", None) if settings else None
    except Exception:
        logger.debug("[kokoro] could not read the persisted engine choice", exc_info=True)
        return None
    if value in ENGINE_IDS:
        return value
    if value:
        logger.warning("[kokoro] ignoring unknown persisted engine %r", value)
    return None


def save_persisted_engine(engine_id: str) -> None:
    """Remember the user's choice. Never raises: a failed write is not fatal."""
    try:
        import db.database as _db
        from db.models import UserSettings
        from sqlmodel import Session

        with Session(_db.engine) as session:
            settings = session.get(UserSettings, 1)
            if settings is None:
                settings = UserSettings(id=1)
                session.add(settings)
            settings.tts_engine = engine_id
            session.commit()
    except Exception:
        logger.warning("[kokoro] could not persist the engine choice", exc_info=True)


# --------------------------------------------------------------------------
# The manager
# --------------------------------------------------------------------------

_applier: Callable[[Any], None] | None = None


def register_applier(applier: Callable[[Any], None]) -> None:
    """Register the callback that pushes a new engine into the routers."""
    global _applier
    _applier = applier


class EngineManager:
    """Builds, probes and swaps the live Kokoro engine."""

    def __init__(
        self,
        local_builder: Callable[[str], tuple[Any | None, str | None]] = build_local,
        remote_builder: Callable[..., tuple[Any | None, str | None]] = build_remote,
        torch_probe: Callable[[], dict[str, Any]] | None = None,
        remote_probe: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._local_builder = local_builder
        self._remote_builder = remote_builder
        # Both probes are injectable, and both the selector and the startup path
        # go through these same two callables: reading cuda_available from one
        # source while a switch consulted another is how a machine with no GPU
        # could still be offered the GPU card.
        self._torch_probe = torch_probe or kokoro_runtime.probe_local_torch
        self._remote_probe = remote_probe or modal_remote.probe
        self._lock = threading.RLock()
        self._current: Any | None = None
        self._active: str | None = None
        self._switching = False
        self._phase = PHASE_IDLE
        self._warmup_thread: threading.Thread | None = None

    # -- reads ------------------------------------------------------------

    def current(self) -> Any | None:
        """The live engine object, or ``None`` before the first build."""
        with self._lock:
            return self._current

    def active(self) -> str | None:
        with self._lock:
            return self._active

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    def state(self, options: list[EngineOption] | None = None) -> dict[str, Any]:
        """The payload behind ``GET /api/system/engine``."""
        with self._lock:
            return {
                "selected": load_persisted_engine(),
                "active": self._active,
                "switching": self._switching,
                "phase": self._phase,
                "options": [o.as_dict() for o in (options if options is not None else self.options())],
            }

    def availability(self, engine_id: str) -> EngineOption:
        """Whether one engine can run here, from that engine's own probe.

        Deliberately per-engine: asking about the local engines must not touch
        the network. Probing Modal to decide whether to start Kokoro on the CPU
        would make the local-first path depend on the internet, which is exactly
        the property this backend is built to keep.
        """
        if engine_id in (CPU, GPU):
            return self._local_availability(engine_id)
        if engine_id == MODAL:
            return self._modal_availability()
        raise UnknownEngine(f"Unknown engine {engine_id!r}; expected one of {', '.join(ENGINE_IDS)}")

    def _local_availability(self, engine_id: str) -> EngineOption:
        info = self._torch_probe()
        torch_ok = info.get("torch_version") is not None
        if engine_id == CPU:
            available = torch_ok
            reason = None if torch_ok else f"PyTorch unavailable: {info.get('error')}"
        else:
            available = bool(info.get("cuda_available"))
            if available:
                reason = None
            elif not torch_ok:
                reason = f"PyTorch unavailable: {info.get('error')}"
            else:
                reason = "No CUDA GPU detected"
        return EngineOption(engine_id, ENGINE_LABELS[engine_id], available, reason)

    def _modal_availability(self) -> EngineOption:
        remote = self._remote_probe(timeout_s=OPTIONS_PROBE_SECONDS)
        available = bool(remote.get("configured") and remote.get("reachable"))
        reason = None if available else _modal_reason(remote)
        return EngineOption(MODAL, ENGINE_LABELS[MODAL], available, reason)

    def options(self) -> list[EngineOption]:
        """Every engine, with the reason each one is or is not usable.

        This is the selector's data source, so it probes all three — including
        Modal, because the UI has to be able to say *why* the cloud card is
        greyed out.
        """
        return [self.availability(engine_id) for engine_id in ENGINE_IDS]

    def playback_phase(self) -> str | None:
        """What to tell the reader about the GPU before it starts playing.

        ``None`` means "nothing worth saying": the live engine is local, or a
        Modal container is already warm. Otherwise it is the phase vocabulary the
        UI renders, and a cold Modal container with no warm-up in flight reports
        ``starting`` because that is exactly what it is doing.
        """
        engine = self._current
        if not isinstance(engine, modal_remote.ModalKokoroClient):
            return None
        if engine.is_warm():
            return None
        with self._lock:
            if self._phase in (PHASE_STARTING, PHASE_WARMING_UP):
                return self._phase
        return PHASE_STARTING

    # -- switching --------------------------------------------------------

    def switch(self, engine_id: str, persist: bool = True, background_warmup: bool = True) -> Any:
        """Make ``engine_id`` live, or raise without disturbing the current one.

        Raises :class:`UnknownEngine` (caller answers 400),
        :class:`EngineUnavailable` (409) or :class:`EngineBuildError` (503). In
        every failure case the previously live engine is still live.
        """
        if engine_id not in ENGINE_IDS:
            raise UnknownEngine(
                f"Unknown engine {engine_id!r}; expected one of {', '.join(ENGINE_IDS)}"
            )

        with self._lock:
            option = self.availability(engine_id)
            if not option.available:
                raise EngineUnavailable(engine_id, option.reason or "unavailable")

            self._switching = True
            try:
                built = self._build(engine_id)
            finally:
                self._switching = False

            previous = self._current
            self._current = built
            self._active = engine_id
            self._record_runtime_state(engine_id, built)
            self._phase = PHASE_IDLE

        self._publish(built)
        self._release(previous, engine_id)

        if engine_id == MODAL and background_warmup:
            self._start_warmup(built)
        if persist:
            save_persisted_engine(engine_id)
        logger.info("[kokoro] engine switched to %s", engine_id)
        return built

    def _build(self, engine_id: str) -> Any:
        if engine_id == MODAL:
            client, error = self._remote_builder(False)
        else:
            device = "cuda" if engine_id == GPU else "cpu"
            client, error = self._local_builder(device)
        if client is None:
            raise EngineBuildError(error or f"could not build the {engine_id} engine")
        return client

    def _release(self, previous: Any | None, new_engine: str) -> None:
        """Drop the old local pipeline, and free its VRAM before a GPU build.

        Called *after* the swap so a slow free never blocks the reader, and only
        for local engines: a Modal client holds no local GPU memory.
        """
        if previous is None or previous is self._current:
            return
        if isinstance(previous, modal_remote.ModalKokoroClient):
            return
        del previous
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            logger.debug("[kokoro] could not empty the CUDA cache", exc_info=True)

    def _publish(self, engine: Any) -> None:
        if _applier is None:
            logger.debug("[kokoro] no applier registered; routers keep the old engine")
            return
        try:
            _applier(engine)
        except Exception:
            logger.exception("[kokoro] pushing the new engine into the routers failed")

    def _record_runtime_state(self, engine_id: str, engine: Any) -> None:
        state = kokoro_runtime.runtime
        if engine_id == MODAL:
            state.record_remote()
        else:
            state.record_local(
                device="cuda" if engine_id == GPU else "cpu",
                model_repo=KOKORO_MODEL_REPO,
            )

    # -- startup ----------------------------------------------------------

    def startup(self, env_backend: str | None = None, persisted: str | None = None) -> Any | None:
        """Pick the engine at process start.

        Order: the user's persisted choice, then ``KOKORO_BACKEND``, then local.
        Every step falls through to the next on failure, and the reason is
        recorded so ``/api/system/capabilities`` can show it. This never raises:
        a process that cannot synthesise still has to serve the library.
        """
        requested = kokoro_runtime.normalize_backend(env_backend)
        kokoro_runtime.runtime.requested_backend = requested

        candidate = persisted if persisted is not None else load_persisted_engine()
        if candidate in ENGINE_IDS:
            try:
                logger.info("[kokoro] honouring the persisted engine choice %r", candidate)
                return self.switch(candidate, persist=False, background_warmup=False)
            except (EngineUnavailable, EngineBuildError) as exc:
                logger.warning(
                    "[kokoro] persisted engine %r is not usable (%s); falling back",
                    candidate,
                    exc,
                )

        for engine_id in self._env_candidates(requested):
            try:
                return self.switch(engine_id, persist=False, background_warmup=False)
            except (EngineUnavailable, EngineBuildError) as exc:
                logger.warning("[kokoro] engine %r unavailable at startup: %s", engine_id, exc)

        kokoro_runtime.runtime.record_failure("No Kokoro backend available")
        logger.error(
            "[kokoro] no Kokoro backend is available, TTS will produce no audio: %s",
            kokoro_runtime.runtime.error,
        )
        return None

    def _env_candidates(self, requested: str) -> list[str]:
        """Engines to try, in order, for a given ``KOKORO_BACKEND`` value."""
        if requested == "remote":
            return [MODAL, GPU, CPU]
        if requested == "auto":
            # Modal only when the probe says it answers; otherwise local. The
            # GPU is preferred over the CPU when this machine actually has one.
            remote = self._remote_probe(timeout_s=5.0)
            cuda = bool(self._torch_probe().get("cuda_available"))
            preferred = MODAL if remote["reachable"] else (GPU if cuda else CPU)
            return [preferred, GPU, CPU] if preferred == MODAL else [preferred, CPU]
        return [GPU, CPU]

    # -- modal warm-up phase ---------------------------------------------

    def _start_warmup(self, client: Any) -> None:
        """Report ``starting`` → ``warming_up`` → ``ready`` while Modal boots.

        The warm-up is a real (tiny) synthesis rather than a spawn: "the
        container answered" is the only signal that means the user's next
        sentence will not have to wait, and a spawned call reports nothing back.
        """
        with self._lock:
            self._phase = PHASE_STARTING
            if self._warmup_thread is not None and self._warmup_thread.is_alive():
                return
            thread = threading.Thread(
                target=self._watch_warmup, args=(client,), name="kokoro-warmup", daemon=True
            )
            self._warmup_thread = thread
        thread.start()

    def _watch_warmup(self, client: Any) -> None:
        deadline = time.monotonic() + WARMUP_WATCH_SECONDS
        finished = threading.Event()

        def warm():
            try:
                client(modal_remote.WARMUP_TEXT)
            except Exception:
                logger.warning("[kokoro] Modal warm-up call failed", exc_info=True)
            finally:
                finished.set()

        worker = threading.Thread(target=warm, name="kokoro-warmup-call", daemon=True)
        worker.start()

        while not finished.is_set() and time.monotonic() < deadline:
            runners = _runner_count(client)
            with self._lock:
                if runners and runners > 0 and self._phase == PHASE_STARTING:
                    self._phase = PHASE_WARMING_UP
            finished.wait(WARMUP_POLL_SECONDS)

        with self._lock:
            self._phase = PHASE_READY
        logger.info("[kokoro] Modal warm-up %s", "finished" if finished.is_set() else "timed out")


def _runner_count(client: Any) -> int | None:
    """Live GPU container count, or ``None`` when the API cannot say."""
    counter = getattr(client, "runner_count", None)
    if counter is None:
        return None
    try:
        return counter()
    except Exception:
        return None


def _modal_reason(remote: dict[str, Any]) -> str:
    if not remote.get("credentials_present") and not remote.get("health_url"):
        return "Modal not set up"
    if not remote.get("configured"):
        return "Modal not set up"
    return f"Modal unreachable — {remote.get('error') or 'no response'}"


manager = EngineManager()
