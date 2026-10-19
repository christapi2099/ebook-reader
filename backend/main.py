import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Before every project import below: db.database, services.modal_remote and
# services.engine_manager read some variables at import time, and a later load
# would leave backend/.env unable to set them (see env_file.py).
from env_file import load_env_file

load_env_file()

from db.database import create_engine_and_tables
import storage
from routers import documents, library
from routers import tts as tts_router
from routers import voices as voices_router
from routers import mp3 as mp3_router
from routers import precompiled_audio as precompiled_audio_router
from routers import bookmarks as bookmarks_router
from routers import folders as folders_router
from routers import user as user_router
from routers import system as system_router
from routers import renders as renders_router
from services.tts_engine import TTSEngine
from services import tts_engine
from services import audio_cache, engine_manager, kokoro_runtime, render_triggers, render_worker

logger = logging.getLogger(__name__)

# The model identity lives in services/engine_manager.py, which owns the engine
# choice now; main only decides *when* to start one.


def _init_kokoro() -> Any | None:
    """Choose the Kokoro backend at startup.

    Thin wrapper over :mod:`services.engine_manager`, which owns the choice from
    here on — including the device, which it picks from the same probe the
    Settings selector consults. Order: the engine persisted in Settings, then
    ``KOKORO_BACKEND`` (``local`` → GPU if this machine has one, else CPU;
    ``remote`` → Modal; ``auto`` → Modal only when a probe says it answers), then
    local. Every failure path falls back so the reader keeps working without a
    network.
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
    # The render worker synthesizes with whatever engine is live, so it must
    # follow every switch (and be cleared when there is none).
    tts_engine.set_render_engine(TTSEngine(kokoro) if kokoro is not None else None)
    precompiled_audio_router.set_kokoro(kokoro)


engine_manager.register_applier(_apply_kokoro)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine_and_tables()
    storage.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    # Process-first: GC, reconcile missing artifacts, requeue stale fingerprints.
    _apply_kokoro(_init_kokoro())
    # After the engine is live: the sweep compares books with the running engine family.
    render_triggers.startup_sweep()
    # Background render worker (no-op when PROCESSOR_ENABLED is 0/false).
    render_worker.start_worker()
    # Audio cache eviction lives here, not in the write path: one sweep at
    # startup, then the periodic task below for the life of the process, so
    # every writer is covered instead of only the flows we remembered
    # (services/audio_cache.py, and §7 of the synthesis-strategy research,
    # explain why that distinction matters). Both are safe with an empty cache
    # and with a database that already sits under the cap.
    await audio_cache.sweep_once(engine)
    sweeper = audio_cache.start_periodic_sweep(engine)
    # One worker, module-level: precompile jobs run one chapter at a time,
    # not one task per POST .../build — see routers/precompiled_audio.py's
    # module docstring for the CUDA-OOM this fixes.
    precompiled_audio_router.start_queue_worker()
    # Detects a precompile job that has stopped reporting progress (a hung
    # ffmpeg/CUDA call, not a dead process — see start_queue_worker()'s own
    # _reset_orphaned_jobs() sweep for the dead-process case).
    precompiled_audio_router.start_stall_watchdog()
    try:
        yield
    finally:
        render_worker.stop_worker()
        # Cancelled and awaited on shutdown so the task cannot outlive the
        # engine, and a sweep in flight cannot keep the process alive.
        await audio_cache.stop_periodic_sweep(sweeper)
        await precompiled_audio_router.stop_queue_worker()
        await precompiled_audio_router.stop_stall_watchdog()


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
app.include_router(precompiled_audio_router.router)
app.include_router(bookmarks_router.router)
app.include_router(folders_router.router)
app.include_router(system_router.router)
app.include_router(renders_router.router)

# check_dir=False: lifespan creates the directory, so importing main never depends on it.
app.mount("/uploads", StaticFiles(directory=storage.UPLOADS_DIR, check_dir=False), name="uploads")


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
