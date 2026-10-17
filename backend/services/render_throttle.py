"""Worker etiquette for the background render worker.

* ``await_playback_idle``: yield to live playback, with a cap.
* ``TaskTracker`` / ``modal_batch_allowed``: batch to Modal only when idle and
  no render synthesis task has been running over 10 s.
* ``preview_pool``: voice preview's own 1-worker pool, so a preview is served
  within one sentence while a render saturates ``_synthesis_pool``.
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable

PLAYBACK_WAIT_CAP_S = 5.0
MODAL_MAX_TASK_S = 10.0
_POLL_S = 0.05

preview_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-preview")


def _engine_waiting() -> int:
    from services import tts_engine

    return tts_engine._playback_waiting


async def await_playback_idle(
    cap_s: float = PLAYBACK_WAIT_CAP_S,
    *,
    waiting: Callable[[], int] | None = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> float:
    """Wait while playback needs the synth worker; give up after ``cap_s``.

    Returns the seconds waited. The clock, sleeper and counter are injectable.
    """
    waiting = waiting or _engine_waiting
    start = now()
    while waiting() > 0:
        if now() - start >= cap_s:
            break
        await sleep(_POLL_S)
    return now() - start


class TaskTracker:
    """Tracks the start times of in-flight render synthesis tasks."""

    def __init__(self, now: Callable[[], float] = time.monotonic):
        self._now = now
        self._lock = threading.Lock()
        self._active: dict[int, float] = {}
        self._next = 0

    def start(self) -> int:
        with self._lock:
            self._next += 1
            self._active[self._next] = self._now()
            return self._next

    def finish(self, token: int) -> None:
        with self._lock:
            self._active.pop(token, None)

    def longest_running_s(self) -> float:
        with self._lock:
            if not self._active:
                return 0.0
            return self._now() - min(self._active.values())


render_tasks = TaskTracker()


def modal_batch_allowed(
    *,
    waiting: Callable[[], int] | None = None,
    tracker: TaskTracker | None = None,
    max_task_s: float = MODAL_MAX_TASK_S,
) -> bool:
    """True only when no playback waits and no task has run over ``max_task_s``."""
    waiting = waiting or _engine_waiting
    tracker = tracker or render_tasks
    return waiting() == 0 and tracker.longest_running_s() <= max_task_s


async def run_preview(fn: Callable[[], Any]) -> Any:
    """Run a blocking preview synthesis on the dedicated preview pool."""
    return await asyncio.get_running_loop().run_in_executor(preview_pool, fn)
