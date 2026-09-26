import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from db.database import create_engine_and_tables
from routers import documents, library
from routers import tts as tts_router
from routers import voices as voices_router
from routers import mp3 as mp3_router
from routers import bookmarks as bookmarks_router
from routers import folders as folders_router
from routers import user as user_router
from routers import system as system_router
from services import audio_cache, kokoro_runtime, modal_remote

logger = logging.getLogger(__name__)

KOKORO_MODEL_REPO = "hexgrad/Kokoro-82M"
KOKORO_LANG_CODE = "a"
# How long KOKORO_BACKEND=auto may spend proving the remote backend is usable
# before it gives up and starts local. Startup must never hang on the network.
REMOTE_STARTUP_PROBE_SECONDS = 5.0


def _load_env_file() -> None:
    """Load ``backend/.env`` if python-dotenv is installed and the file is there.

    Real environment variables win over the file (``override=False``), and a
    missing file or a missing python-dotenv is not an error: the app is meant to
    run on nothing but its defaults.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.debug("python-dotenv not installed; using the process environment only")
        return
    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        load_dotenv(env_path, override=False)
        logger.info("[config] loaded %s", env_path.name)
    else:
        logger.info("[config] no %s found; using the process environment only", env_path.name)


def _init_local_kokoro() -> tuple[Any | None, str | None, str | None]:
    """Build the in-process Kokoro pipeline.

    Returns ``(pipeline, device, error)``. The error string is kept so the
    capability endpoint can explain *why* there is no local pipeline instead of
    reporting a bare ``null``.
    """
    try:
        import torch
        from kokoro import KPipeline

        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        # The device is computed once, logged, returned to the caller and then
        # actually passed to KPipeline — never thrown away.
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("[kokoro] initializing local pipeline on %s", device)
        pipeline = KPipeline(
            lang_code=KOKORO_LANG_CODE,
            repo_id=KOKORO_MODEL_REPO,
            device=device,
        )
    except Exception as exc:
        # Broad on purpose (a broken CUDA install can raise almost anything), but
        # loud: the traceback is the whole point, and the reason is recorded so
        # /api/system/capabilities can show it.
        logger.exception("[kokoro] local pipeline failed to initialise")
        return None, None, f"{type(exc).__name__}: {exc}"

    logger.info("[kokoro] local pipeline ready on %s", device)
    return pipeline, device, None


def _init_remote_kokoro(requested: str) -> tuple[Any | None, str | None]:
    """Build the Modal client, or explain why it could not be built."""
    config = modal_remote.RemoteConfig.from_env()
    transport = modal_remote.resolve_transport(config)
    if transport is None:
        return None, (
            "no Modal credentials (MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or ~/.modal.toml) "
            "and no MODAL_KOKORO_HEALTH_URL"
        )
    if requested == "auto":
        status = modal_remote.probe(config=config, timeout_s=REMOTE_STARTUP_PROBE_SECONDS)
        if not status["reachable"]:
            return None, f"remote backend not reachable: {status['error']}"
    return modal_remote.ModalKokoroClient(config=config), None


def _init_kokoro() -> Any | None:
    """Choose the Kokoro backend: ``local`` (default), ``remote`` or ``auto``.

    ``auto`` means "remote only when credentials exist *and* a probe says the
    deployed app answers"; every failure path falls back to local so the reader
    keeps working without a network.
    """
    requested = kokoro_runtime.normalize_backend(os.environ.get("KOKORO_BACKEND"))
    state = kokoro_runtime.runtime
    state.requested_backend = requested
    remote_error: str | None = None

    if requested in ("remote", "auto"):
        try:
            client, remote_error = _init_remote_kokoro(requested)
        except Exception as exc:
            logger.exception("[kokoro] building the Modal remote client failed")
            client, remote_error = None, f"{type(exc).__name__}: {exc}"
        if client is not None:
            state.record_remote()
            logger.info(
                "[kokoro] using remote backend transport=%s app=%s function=%s",
                client.transport,
                client.config.app_name,
                client.config.function_name,
            )
            return client
        state.remote_error = remote_error
        logger.warning(
            "[kokoro] KOKORO_BACKEND=%s could not use the remote backend (%s); falling back to local",
            requested,
            remote_error,
        )

    pipeline, device, local_error = _init_local_kokoro()
    if pipeline is not None:
        state.record_local(device=device or "cpu", model_repo=KOKORO_MODEL_REPO)
        return pipeline

    state.record_failure(local_error or remote_error or "no Kokoro backend available")
    logger.error(
        "[kokoro] no Kokoro backend is available, TTS will produce no audio: %s", state.error
    )
    return None


_load_env_file()


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine_and_tables()
    Path("uploads").mkdir(exist_ok=True)
    kokoro = _init_kokoro()
    tts_router.set_kokoro(kokoro)
    voices_router.set_kokoro(kokoro)
    mp3_router.set_kokoro(kokoro)
    # Audio cache eviction lives here, not in the write path: one sweep at
    # startup, then the periodic task below for the life of the process, so
    # every writer is covered instead of only the flows we remembered
    # (services/audio_cache.py, and §7 of the synthesis-strategy research,
    # explain why that distinction matters). Both are safe with an empty cache
    # and with a database that already sits under the cap.
    await audio_cache.sweep_once(engine)
    sweeper = audio_cache.start_periodic_sweep(engine)
    try:
        yield
    finally:
        # Cancelled and awaited on shutdown so the task cannot outlive the
        # engine, and a sweep in flight cannot keep the process alive.
        await audio_cache.stop_periodic_sweep(sweeper)


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(library.router)
app.include_router(user_router.router)
app.include_router(tts_router.router)
app.include_router(voices_router.router)
app.include_router(mp3_router.router)
app.include_router(bookmarks_router.router)
app.include_router(folders_router.router)
app.include_router(system_router.router)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/health")
async def health():
    # Additive only: `status` keeps its original value and meaning. The extra
    # keys say which backend is live, which is the first thing to check when the
    # reader is silent. Details live on GET /api/system/capabilities.
    state = kokoro_runtime.runtime
    return {
        "status": "ok",
        "backend": state.active_backend,
        "device": state.device,
        "synthesis_available": state.active_backend != "none",
    }
