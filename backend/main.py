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
from services import audio_cache, engine_manager, kokoro_runtime

logger = logging.getLogger(__name__)

# The model identity lives in services/engine_manager.py, which owns the engine
# choice now; main only decides *when* to start one.


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
    """Build the in-process Kokoro pipeline on the device torch reports.

    Kept as the single place the *device* is decided for the startup path; the
    actual build lives in ``engine_manager.build_local`` so the runtime switch in
    Settings can ask for an explicit device instead of taking pot luck.
    Returns ``(pipeline, device, error)``.
    """
    # The manager owns the "is there a GPU" answer, so asking the same probe it
    # uses keeps the startup device and the Settings selector from disagreeing.
    torch_info = kokoro_runtime.probe_local_torch()
    device = "cuda" if torch_info["cuda_available"] else "cpu"
    pipeline, error = engine_manager.build_local(device)
    if pipeline is None:
        return None, None, error
    return pipeline, device, None


def _init_kokoro() -> Any | None:
    """Choose the Kokoro backend at startup.

    Thin wrapper over :mod:`services.engine_manager`, which owns the choice from
    here on. Order: the engine persisted in Settings, then ``KOKORO_BACKEND``
    (``local`` → GPU if this machine has one, else CPU; ``remote`` → Modal;
    ``auto`` → Modal only when a probe says it answers), then local. Every
    failure path falls back so the reader keeps working without a network.
    """
    return engine_manager.manager.startup(os.environ.get("KOKORO_BACKEND"))


def _apply_kokoro(kokoro: Any) -> None:
    """Push the live engine into every router that holds one.

    Registered with the engine manager, so a runtime switch in Settings reaches
    the WebSocket, voice preview and export paths through exactly this function
    instead of three ad-hoc assignments scattered around startup.
    """
    tts_router.set_kokoro(kokoro)
    voices_router.set_kokoro(kokoro)
    mp3_router.set_kokoro(kokoro)


engine_manager.register_applier(_apply_kokoro)


_load_env_file()


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine_and_tables()
    Path("uploads").mkdir(exist_ok=True)
    _apply_kokoro(_init_kokoro())
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
