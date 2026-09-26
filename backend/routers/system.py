"""System capability probe.

Reports what the backend can actually do right now — the device Kokoro was
built on, what torch sees, and whether the remote Modal backend is configured
and reachable — instead of leaving the frontend to guess (or to invent a
"Needs CUDA" pill with nothing behind it).
"""
from typing import Any

from fastapi import APIRouter

from services import kokoro_runtime

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/capabilities")
def get_capabilities() -> dict[str, Any]:
    """Live probe of local device, torch build and remote backend reachability."""
    return kokoro_runtime.capabilities()
