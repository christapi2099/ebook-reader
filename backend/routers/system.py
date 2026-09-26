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
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from services import engine_manager, kokoro_runtime

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/capabilities")
def get_capabilities() -> dict[str, Any]:
    """Live probe of local device, torch build and remote backend reachability."""
    return kokoro_runtime.capabilities()


class SwitchRequest(BaseModel):
    engine: str


@router.get("/engine")
def get_engine() -> dict[str, Any]:
    """Which engine is live, which one the user picked, and what is available."""
    return engine_manager.manager.state()


@router.post("/engine")
async def set_engine(body: SwitchRequest) -> dict[str, Any]:
    """Switch the live synthesis engine.

    400 for an unknown id, 409 when the probe says this machine cannot run it
    (with the probe's own reason), 503 when the build itself failed. In every
    failure case the previously live engine keeps running, so a failed switch
    never leaves the reader silent.
    """
    try:
        # Building an engine takes seconds (a model load, or a container boot),
        # so it must not run on the event loop: one Switch click would otherwise
        # freeze playback and every other request for its whole duration.
        await run_in_threadpool(engine_manager.manager.switch, body.engine)
    except engine_manager.UnknownEngine as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except engine_manager.EngineUnavailable as exc:
        raise HTTPException(status_code=409, detail=exc.reason)
    except engine_manager.EngineBuildError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return engine_manager.manager.state()
