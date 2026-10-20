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
REQUEST_TIMEOUT_S = 600.0   # one sentence; a child silent for this long is hung and replaced
WORKER_RAM_GB = 2.5         # host RAM per worker process (measured ~2 GB PSS each)
OOM_MARKERS = ("out of memory", "alloc_failed", "memory_error", "hiperroroutofmemory",
               "memory allocation")


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
        logger.warning("[render] could not read free VRAM; limiting to one GPU worker",
                       exc_info=True)
        return None


def mem_available_bytes() -> Optional[int]:
    """Host ``MemAvailable`` from /proc/meminfo, or ``None`` when unreadable."""
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def ram_worker_cap(available: Optional[int], per_worker: Optional[int] = None) -> Optional[int]:
    """Workers that fit in host RAM (``None`` = unknown, no RAM cap); never below 1."""
    if available is None:
        return None
    return max(1, int(available // (per_worker or int(WORKER_RAM_GB * GIB))))


def max_gpu_workers() -> int:
    """Ceiling on GPU workers: 4 (6 measured slower, 8 out of memory on 12 GB), unless
    ``RENDER_GPU_WORKERS_MAX`` raises or lowers it."""
    from services import env_config
    return env_config.positive_int("RENDER_GPU_WORKERS_MAX", DEFAULT_GPU_WORKERS)


def is_oom(exc: BaseException) -> bool:
    """torch's OutOfMemoryError, or a HIP/ROCm RuntimeError that only says so in its text."""
    if type(exc).__name__ == "OutOfMemoryError":
        return True
    text = str(exc).lower()
    return any(m in text for m in OOM_MARKERS)


# ------------------------------------------------------------------ child process
def _worker_main(conn, device: str) -> None:  # pragma: no cover - runs in a spawned process
    import signal

    from services import engine_manager
    from services.tts_engine import TTSEngine

    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C hits the whole group; the parent stops us
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

    def request(self, text: str, voice: str, timeout: float = REQUEST_TIMEOUT_S) -> tuple:
        """One sentence. A dead or hung child (no answer within ``timeout``) is killed and
        reported as ``("dead", why)`` so the pool replaces it."""
        try:
            self.conn.send((text, voice))
            if not self.conn.poll(timeout):
                self.proc.terminate()
                return ("dead", f"GPU worker hung (no answer in {timeout:.0f}s)")
            return self.conn.recv()
        except (EOFError, BrokenPipeError, OSError):
            return ("dead", "GPU worker process died")

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
                 per_worker_bytes: Optional[int] = None,
                 avail_ram: Optional[Callable[[], Optional[int]]] = None):
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

        self._failed: Optional[str] = None  # set once the pool cannot serve any request

        workers = min(workers, max_gpu_workers())
        free = free_vram(device)
        if free is None:
            if workers > 1:
                logger.warning("[render] free VRAM unknown on %s: GPU workers limited to 1", device)
            workers = 1
        else:
            cap = vram_worker_cap(free, per_worker_bytes)
            if cap < workers:
                logger.warning("[render] GPU workers reduced %d -> %d: %.1f GiB VRAM free, "
                               "%.1f GiB reserved, ~%.1f GiB per worker",
                               workers, cap, free / GIB, RESERVE_VRAM_GB,
                               (per_worker_bytes or worker_vram_bytes()) / GIB)
                workers = cap
        ram_cap = ram_worker_cap((avail_ram or mem_available_bytes)())
        if ram_cap is not None and ram_cap < workers:
            logger.warning("[render] GPU workers reduced %d -> %d: host RAM available for ~%.1f "
                           "GiB per worker", workers, ram_cap, WORKER_RAM_GB)
            workers = ram_cap
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

    def _fail(self, why: str) -> None:
        self._failed = self._failed or why
        logger.error("[render] GPU worker pool unusable: %s", why)

    def _acquire(self):
        """An idle worker. Never blocks forever: once the pool is failed or closed, every waiter
        raises instead (a hung ``get`` here used to hang the render's shutdown too)."""
        while True:
            if self._failed:
                raise RuntimeError(f"GPU worker pool unusable: {self._failed}")
            try:
                return self._idle.get(timeout=0.5)
            except queue.Empty:
                continue

    def _replace(self, h) -> Optional[Any]:
        """Drop worker ``h`` from rotation (it is dead, hung or out of memory), close it and
        start a fresh one. ``None`` when the replacement cannot start: the active count then
        drops, and with no worker left the pool is failed."""
        with self._lock:
            if h in self._all:
                self._all.remove(h)
        h.close()
        new = self._respawn()
        if new is None:
            with self._lock:
                self.active -= 1
                if self.active <= 0:
                    self._fail("no GPU worker could be restarted")
        return new

    def synthesize(self, text: str, voice: str) -> tuple[np.ndarray, Optional[list]]:
        oom_alone = 0   # OOMs of this sentence on the LAST remaining worker only
        while True:
            h = self._acquire()
            kind, *rest = h.request(text, voice)
            if kind == "ok":
                self._idle.put(h)
                audio, tokens = rest
                if audio is None:
                    raise RuntimeError("GPU worker produced no audio")
                return audio, tokens
            if kind == "oom":
                with self._lock:
                    self.oom_events += 1
                    step_down = self.active > 1
                    if step_down:
                        self.active -= 1
                        self._all.remove(h)
                        logger.warning("[render] GPU out of memory; GPU workers %d -> %d "
                                       "(retrying the sentence)", self.active + 1, self.active)
                if step_down:
                    h.close()       # never back on _idle: the process has exited
                    continue
                oom_alone += 1
                logger.warning("[render] GPU out of memory with one worker left; restarting it")
                new = self._replace(h)
                if new is not None:
                    self._idle.put(new)
                if oom_alone > MAX_OOM_RETRIES:
                    raise RuntimeError(f"GPU out of memory with one worker: {rest[0]}")
                if new is None:
                    raise RuntimeError(f"GPU out of memory and the worker could not restart: "
                                       f"{rest[0]}")
                continue
            if kind == "dead":      # crashed or hung for a non-OOM reason: replace, then fail
                new = self._replace(h)  # this sentence (the next one gets a healthy worker)
                if new is not None:
                    self._idle.put(new)
                raise RuntimeError(f"GPU worker error: {rest[0] if rest else kind}")
            self._idle.put(h)       # "err": a Python exception in a still-healthy child
            raise RuntimeError(f"GPU worker error: {rest[0] if rest else kind}")

    def _respawn(self):
        """A fresh worker process, or ``None`` if it cannot start."""
        try:
            h = self._spawn(self.device)
            ok, _ = h.wait_ready()
        except Exception:  # noqa: BLE001
            logger.exception("[render] could not start a replacement GPU worker")
            return None
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
            self._failed = self._failed or "pool closed"
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
