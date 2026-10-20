"""A small pool of spawned processes, each owning one torch Kokoro (``KPipeline``) on the GPU.

Why processes and not threads (measured 2026-10-20, RX 6700 XT, ROCm, MIOpen off, 80 real
sentences per worker, ``scripts/bench_gpu_synth.py``): 4 processes reach about 45x real time,
4 threads in one process (one pipeline each) only about 29x (1 worker: 24x). misaki g2p and the
Python side of ``KPipeline`` hold the GIL, so threads serialise on it; processes do not.

Wire format is deliberately tiny: the parent sends ``(text, voice)``; the child answers
``("ok", float32 audio, kokoro_tokens)``, ``("oom", message)`` or ``("err", traceback)``. Tokens
are produced by the same ``TTSEngine.synthesize_for_render`` code the serial path uses, so the
aligner sees identical input. The pool never touches the DB, the job or any file.

Safety: the number of workers is capped by free VRAM before anything starts
(:func:`vram_worker_cap`), and a worker that hits an out-of-memory error is retired (its process
exits, freeing its ~1.8 GB) so the render continues with one worker fewer, down to one. There is
no tuning on GPU busy%: throughput saturates at about 4 workers on this card.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import traceback
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

GIB = 1024 ** 3
DEFAULT_GPU_WORKERS = 4
# Measured: each process holds about 1.8 GB of VRAM (model + context + activations).
DEFAULT_WORKER_VRAM_GB = 1.8
RESERVE_VRAM_GB = 1.0   # always left free for the live engine, the display and fragmentation
MAX_OOM_RETRIES = 3     # per sentence, for the last remaining worker
READY_TIMEOUT_S = 180.0


def worker_vram_bytes() -> int:
    """Per-worker footprint; ``RENDER_GPU_WORKER_VRAM_GB`` overrides the measured default."""
    from services import env_config
    gb = env_config.positive_number(os.environ.get("RENDER_GPU_WORKER_VRAM_GB"),
                                    "RENDER_GPU_WORKER_VRAM_GB") or DEFAULT_WORKER_VRAM_GB
    return int(gb * GIB)


def vram_worker_cap(free_bytes: int, per_worker: Optional[int] = None,
                    reserve: int = int(RESERVE_VRAM_GB * GIB)) -> int:
    """``floor((free - reserve) / per_worker)``, never below 1 (one worker is always allowed)."""
    per = per_worker or worker_vram_bytes()
    return max(1, int((free_bytes - reserve) // per))


def free_vram_bytes(device: str) -> Optional[int]:
    """Free VRAM on ``device`` via ``torch.cuda.mem_get_info``; ``None`` if unreadable."""
    try:
        import torch
        free, _total = torch.cuda.mem_get_info(torch.device(device))
        return int(free)
    except Exception:  # noqa: BLE001
        logger.warning("[render] could not read free VRAM; not capping GPU workers", exc_info=True)
        return None


def is_oom(exc: BaseException) -> bool:
    """torch's OutOfMemoryError, or a HIP/ROCm RuntimeError that only says so in its text."""
    if type(exc).__name__ == "OutOfMemoryError":
        return True
    return "out of memory" in str(exc).lower()


# ------------------------------------------------------------------ child process
def _worker_main(conn, device: str) -> None:  # pragma: no cover - runs in a spawned process
    from services import engine_manager
    from services.tts_engine import TTSEngine

    pipe, err = engine_manager.build_local(device)
    if pipe is None:
        conn.send(("init_err", err))
        return
    engine = TTSEngine(pipe)
    conn.send(("ready", None))
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            return
        if msg is None:
            return
        text, voice = msg
        try:
            audio, _eff, _native, tokens = engine.synthesize_for_render(text, voice, 1.0)
            conn.send(("ok", None if audio is None else np.asarray(audio, dtype=np.float32),
                       tokens))
        except BaseException as exc:  # noqa: BLE001
            if is_oom(exc):
                conn.send(("oom", str(exc)[:300]))
                return  # exit: that frees the process's VRAM; the parent retires us
            conn.send(("err", traceback.format_exc()))


class ProcessWorker:
    """Parent-side handle of one spawned worker."""

    def __init__(self, device: str):
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker_main, args=(child, device), daemon=True,
                                name="kokoro-gpu-worker")
        self.proc.start()
        child.close()

    def wait_ready(self, timeout: float = READY_TIMEOUT_S) -> tuple[bool, Optional[str]]:
        if not self.conn.poll(timeout):
            return False, "timed out loading the model"
        try:
            kind, payload = self.conn.recv()
        except EOFError:
            return False, "worker process died while loading"
        return kind == "ready", payload

    def request(self, text: str, voice: str) -> tuple:
        try:
            self.conn.send((text, voice))
            return self.conn.recv()
        except (EOFError, BrokenPipeError, OSError):
            return ("err", "GPU worker process died")

    def close(self) -> None:
        try:
            self.conn.send(None)
        except (OSError, ValueError):
            pass
        self.proc.join(5)
        if self.proc.is_alive():
            self.proc.terminate()
            self.proc.join(5)
        try:
            self.conn.close()
        except OSError:
            pass


# ------------------------------------------------------------------------- pool
class KokoroProcessPool:
    """``synthesize(text, voice) -> (audio, tokens)``, safe to call from many threads."""

    def __init__(self, device: str, workers: int, *,
                 spawn: Optional[Callable[[str], Any]] = None,
                 free_vram: Optional[Callable[[str], Optional[int]]] = None,
                 per_worker_bytes: Optional[int] = None):
        self.device = device
        spawn = spawn or ProcessWorker  # resolved at call time so tests can substitute them
        free_vram = free_vram or free_vram_bytes
        self._spawn = spawn
        self.requested = workers
        self._idle: "queue.Queue[Any]" = queue.Queue()
        self._lock = threading.Lock()
        self._all: list = []
        self.active = 0
        self.closed = False
        self.oom_events = 0
        self.startup_s = 0.0

        free = free_vram(device)
        if free is not None:
            cap = vram_worker_cap(free, per_worker_bytes)
            if cap < workers:
                logger.warning("[render] GPU workers reduced %d -> %d: %.1f GiB VRAM free, "
                               "%.1f GiB reserved, ~%.1f GiB per worker",
                               workers, cap, free / GIB, RESERVE_VRAM_GB,
                               (per_worker_bytes or worker_vram_bytes()) / GIB)
                workers = cap
        self.planned = workers
        if workers <= 1:
            return  # the caller renders serially with the engine already in memory
        started = time.monotonic()
        handles = [spawn(device) for _ in range(workers)]  # load all models concurrently
        for h in handles:
            ok, err = h.wait_ready()
            if ok:
                self._all.append(h)
                self._idle.put(h)
            else:
                logger.warning("[render] a GPU worker failed to start (%s); continuing with "
                               "fewer", err)
                h.close()
        self.active = len(self._all)
        self.startup_s = time.monotonic() - started
        logger.info("[render] %d GPU worker processes ready in %.1fs", self.active, self.startup_s)
        if self.active < workers:
            logger.warning("[render] GPU workers: %d of %d started", self.active, workers)

    def synthesize(self, text: str, voice: str) -> tuple[np.ndarray, Optional[list]]:
        oom_here = 0
        while True:
            h = self._idle.get()
            kind, *rest = h.request(text, voice)
            if kind == "ok":
                self._idle.put(h)
                audio, tokens = rest
                if audio is None:
                    raise RuntimeError("GPU worker produced no audio")
                return audio, tokens
            if kind == "oom":
                oom_here += 1
                with self._lock:
                    self.oom_events += 1
                    if self.active > 1:
                        self.active -= 1
                        self._all.remove(h)
                        logger.warning("[render] GPU out of memory; GPU workers %d -> %d "
                                       "(retrying the sentence)", self.active + 1, self.active)
                        retired = True
                    else:
                        retired = False
                if retired:
                    h.close()
                    continue
                if oom_here > MAX_OOM_RETRIES:
                    self._idle.put(h)
                    raise RuntimeError(f"GPU out of memory with one worker: {rest[0]}")
                logger.warning("[render] GPU out of memory with one worker left; restarting it "
                               "and retrying")
                with self._lock:
                    if h in self._all:
                        self._all.remove(h)
                h.close()
                h = self._respawn()
                if h is None:
                    raise RuntimeError(f"GPU out of memory and the worker could not restart: "
                                       f"{rest[0]}")
                self._idle.put(h)
                continue
            self._idle.put(h)
            raise RuntimeError(f"GPU worker error: {rest[0] if rest else kind}")

    def _respawn(self):
        """Replace the lone worker that just ran out of memory with a fresh process."""
        h = self._spawn(self.device)
        ok, _ = h.wait_ready()
        if not ok:
            h.close()
            return None
        with self._lock:
            self._all.append(h)
        return h

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            handles, self._all = list(self._all), []
        for h in handles:
            h.close()


class PoolClient:
    """What one render thread holds: the engine-clone surface over the shared pool."""

    def __init__(self, pool: KokoroProcessPool):
        self._pool = pool

    def synthesize_for_render(self, text: str, voice: str = ""):
        audio, tokens = self._pool.synthesize(text, voice)
        return audio, None, tokens
