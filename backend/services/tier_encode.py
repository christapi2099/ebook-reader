"""Chunked, parallel Opus encode of the speed tiers.

libopus is >90% of the tier cost and runs on one core per ffmpeg, so each tier
is cut into ~CHUNK_S pieces encoded by a shared pool of ffmpeg processes, and the
packets are spliced back into ONE Ogg Opus stream per tier.

Per tier, one ffmpeg runs the same decode/atempo chain as the monolithic encode
and streams s16le PCM (libopus gets the exact same input samples). Splits fall on
the 20 ms frame grid (FRAME samples at 24 kHz) placed so the hand-over between
encoders happens near the centre of a run of exact digital zeros (the sentence
gaps), with at least SPLIT_MARGIN zeros on each side. Each chunk's encoder starts WARM samples early so its state has settled; the
warm-up packets are dropped. Packet j of an encoder whose input starts at global
sample s0 (s0 % FRAME == 0) is global packet s0 / FRAME + j, so the kept packets
line up exactly with the monolithic stream: same packet count, same final
granule, same decoded length and timeline. Packet bytes after the first split
differ (the VBR state differs), the decoded audio is an equally good encode.

Memory is flat: each tier's PCM is held for at most ~1.5 chunks plus warm-up, at
most ``2 * workers`` chunks wait for an encoder, and spliced packets are written
to disk in order as soon as they are contiguous.
"""
from __future__ import annotations

import itertools
import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from services import ogg_opus
from services.env_config import positive_int

logger = logging.getLogger(__name__)

SR = 24000
FRAME = 480                 # 20 ms Opus frame at 24 kHz
WARM = SR                   # 1 s of encoder warm-up before each chunk (dropped)
TAIL = 2 * FRAME            # audio past each chunk end so its last kept packet is final
ENCODER_DELAY = 156         # libopus look-ahead at 24 kHz (OpusHead pre-skip 312 / 2)
SPLIT_MARGIN = 240          # exact zeros needed each side of the splice instant (10 ms)
CHUNK_S = 60.0              # target chunk length (seconds of tier audio)
READ_SAMPLES = SR * 4       # PCM read from the atempo pipe at a time
PROGRESS_EVERY_S = 0.5      # min spacing of progress callbacks
CHUNK_TIMEOUT_S = 600.0     # one ~61 s chunk takes ~1 s; past this the ffmpeg is hung


def _cpus() -> int:
    """CPUs this process may run on (affinity / cgroup cpusets), else the machine's count."""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 2


def encode_workers() -> int:
    """Concurrent chunk encodes: ``RENDER_ENCODE_WORKERS`` or ``min(48, cpus // 2)``,
    cpus being the ones this process may use."""
    return positive_int("RENDER_ENCODE_WORKERS", min(48, max(1, _cpus() // 2)))


class ChunkEncodeError(RuntimeError):
    pass


def splice_instant(b: int) -> int:
    """Input sample where a split at packet boundary ``b`` hands over to the next
    encoder: packet b / FRAME decodes input samples from b - ENCODER_DELAY on."""
    return b - ENCODER_DELAY


def find_split(pcm: np.ndarray, offset: int, lo: int, hi: int, target: int) -> int | None:
    """A frame-grid split point ``b`` in [lo, hi] (global samples) whose splice
    instant sits in exact digital silence: at least SPLIT_MARGIN zeros each side,
    as near the centre of the run of zeros as the 20 ms grid allows. The candidate
    nearest ``target`` wins; None if there is none.

    ``pcm[i]`` is global sample ``offset + i``; zeros outside the buffer are not
    assumed, so a run cut by the buffer edge only counts its visible part."""
    a = max(lo - ENCODER_DELAY - SPLIT_MARGIN, offset)
    e = min(hi - ENCODER_DELAY + SPLIT_MARGIN, offset + len(pcm))
    if e - a < 2 * SPLIT_MARGIN:
        return None
    z = np.concatenate(([False], pcm[a - offset:e - offset] == 0, [False])).astype(np.int8)
    d = np.diff(z)
    starts = np.flatnonzero(d == 1) + a
    ends = np.flatnonzero(d == -1) + a          # exclusive
    # splice instant p = b - ENCODER_DELAY must satisfy start + M <= p <= end - M
    bmin = -(-np.maximum(starts + SPLIT_MARGIN + ENCODER_DELAY, lo) // FRAME) * FRAME
    bmax = (np.minimum(ends - SPLIT_MARGIN + ENCODER_DELAY, hi) // FRAME) * FRAME
    ok = bmin <= bmax
    if not ok.any():
        return None
    centre = np.round(((starts[ok] + ends[ok]) / 2 + ENCODER_DELAY) / FRAME).astype(np.int64) * FRAME
    cand = np.clip(centre, bmin[ok], bmax[ok])
    return int(cand[np.argmin(np.abs(cand - target))])


@dataclass(order=True)
class _Job:
    priority: tuple
    tier: "_Tier" = field(compare=False)
    k: int = field(compare=False)
    a: int = field(compare=False)           # first kept sample (global)
    b: int = field(compare=False)           # end of kept samples (global, exclusive)
    s0: int = field(compare=False)          # first encoded sample (a - warm-up)
    last: bool = field(compare=False)
    data: bytes = field(compare=False, repr=False)


class _Tier:
    """Splices one tier's chunks into ``dst`` (written as ``dst.part``) in order."""

    def __init__(self, key: str, chain: str | None, dst: Path, rank: int, expected: int):
        self.key, self.chain, self.dst, self.rank = key, chain, Path(dst), rank
        self.part = self.dst.with_name(self.dst.name + ".part")
        self.expected = expected       # estimated samples (progress only)
        self.lock = threading.Lock()
        self.pending: dict[int, tuple] = {}
        self.next_k = 0
        self.writer = None
        self.file = None
        self.head = None
        self.total = None              # samples produced, set by the producer at EOF
        self.forced = 0                # splits that found no digital silence
        self.chunks = 0
        self.done = False

    def add(self, job: _Job, packets: list[bytes], head: bytes, tags: bytes, serial: int,
            final_granule: int | None, abort: threading.Event) -> bool:
        """Queue one encoded chunk; write every chunk that is now contiguous.
        Returns True when this call finished the tier."""
        with self.lock:
            if abort.is_set():
                return False
            self.pending[job.k] = (packets, job.last, final_granule)
            if self.writer is None:
                self.head = head
                self.file = open(self.part, "wb")
                self.writer = ogg_opus.OggOpusWriter(self.file, serial, head, tags)
            elif head != self.head:
                raise ChunkEncodeError(f"tier {self.key}: chunk {job.k} has a different OpusHead")
            while self.next_k in self.pending:
                pk, last, fg = self.pending.pop(self.next_k)
                self.writer.write(pk)
                self.next_k += 1
                if last:
                    n = self.writer.close(fg)
                    self.file.close()
                    self.file = None
                    self._check(n, fg)
                    os.replace(self.part, self.dst)
                    self.done = True
                    return True
            return False

    def _check(self, packets: int, final_granule: int) -> None:
        """The spliced stream must end exactly where a monolithic encode would."""
        want = ogg_opus.pre_skip(self.head) + 2 * self.total   # 24 kHz input -> 48 kHz granule
        if final_granule != want or not (packets - 1) * 960 < final_granule <= packets * 960:
            raise ChunkEncodeError(
                f"tier {self.key}: spliced stream is inconsistent (granule {final_granule}, "
                f"want {want}, {packets} packets)")

    def close_quietly(self) -> None:
        with self.lock:
            if self.file is not None:
                self.file.close()
                self.file = None


def _encode_chunk(job: _Job, bitrate_k: int) -> tuple[list[bytes], bytes, bytes, int, int | None]:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1",
           "-i", "pipe:0", "-c:a", "libopus", "-b:a", f"{bitrate_k}k", "-f", "ogg", "pipe:1"]
    try:
        r = subprocess.run(cmd, input=job.data, capture_output=True, timeout=CHUNK_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise ChunkEncodeError(f"tier {job.tier.key} chunk {job.k}: ffmpeg timed out after "
                               f"{CHUNK_TIMEOUT_S:.0f} s") from exc
    if r.returncode != 0:
        raise ChunkEncodeError(f"tier {job.tier.key} chunk {job.k}: ffmpeg failed: "
                               f"{r.stderr.decode(errors='replace')[-300:]}")
    serial, packets, granule = ogg_opus.read_packets(r.stdout)
    head, tags, audio = packets[0], packets[1], packets[2:]
    lo = (job.a - job.s0) // FRAME
    hi = len(audio) if job.last else (job.b - job.s0) // FRAME
    if hi > len(audio) or lo >= hi:
        raise ChunkEncodeError(f"tier {job.tier.key} chunk {job.k}: {len(audio)} packets, "
                               f"needed [{lo}, {hi})")
    final = granule + 2 * job.s0 if job.last else None
    return audio[lo:hi], head, tags, serial, final


def encode_tiers(base: Path, tiers: list[tuple[str, str | None, Path]], *, base_samples: int,
                 factors: dict[str, float], workers: int, bitrate_k: int,
                 on_progress=None, chunk_s: float | None = None) -> dict[str, dict]:
    """Encode every ``(key, atempo_chain_or_None, dst)`` tier from ``base`` with a
    shared pool of ``workers`` chunk encoders.

    ``on_progress(fraction)`` is called from the CALLING thread only (it may touch
    the database or raise to abort), at most every PROGRESS_EVERY_S, as chunks are
    spliced. On any failure every output of this call (``dst`` and ``dst.part``)
    is removed, all threads and ffmpeg processes are stopped, and the error is
    re-raised. Returns {key: {chunks, forced_splits, samples}}.
    """
    chunk = max(FRAME, int((chunk_s or CHUNK_S) * SR) // FRAME * FRAME)
    order = sorted(tiers, key=lambda t: factors[t[0]])   # slowest (longest) tier first
    states = [_Tier(key, chain, dst, rank, int(base_samples / factors[key]))
              for rank, (key, chain, dst) in enumerate(order)]
    abort = threading.Event()
    jobs: queue.PriorityQueue = queue.PriorityQueue()
    events: queue.Queue = queue.Queue()
    slots = threading.Semaphore(max(2, 2 * workers))
    procs: list[subprocess.Popen] = []
    procs_lock = threading.Lock()
    seq = itertools.count()

    def fail(exc: BaseException) -> None:
        abort.set()
        events.put(("error", exc))

    def submit(job: _Job) -> bool:
        while not slots.acquire(timeout=0.2):
            if abort.is_set():
                return False
        if abort.is_set():
            slots.release()
            return False
        jobs.put(job)
        return True

    def produce(t: _Tier) -> None:
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(base)]
        if t.chain:
            cmd += ["-filter:a", t.chain]
        cmd += ["-ar", str(SR), "-ac", "1", "-f", "s16le", "pipe:1"]
        try:
            with tempfile.TemporaryFile() as err:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=err)
                with procs_lock:
                    procs.append(proc)

                def exited_cleanly() -> None:
                    """At EOF, before the last chunk is queued: a decoder that died
                    early must fail the tier, never publish a short one."""
                    if proc.wait() != 0 and not abort.is_set():
                        err.seek(0)
                        raise ChunkEncodeError(f"tier {t.key}: ffmpeg decode failed: "
                                               f"{err.read().decode(errors='replace')[-300:]}")

                try:
                    _split_stream(t, proc.stdout, exited_cleanly)
                finally:
                    if abort.is_set():
                        proc.kill()
                    proc.stdout.close()
                    proc.wait()
        except BaseException as exc:  # noqa: BLE001 - handed to the calling thread
            fail(exc)

    def _split_stream(t: _Tier, pipe, exited_cleanly) -> None:
        buf = np.zeros(0, dtype="<i2")
        off = a = k = 0            # buf[0] is global sample `off`; next chunk starts at `a`
        carry = b""
        eof = False
        while not abort.is_set():
            need = a + chunk + chunk // 2 + TAIL + SPLIT_MARGIN
            while not eof and off + len(buf) < need:
                data = pipe.read(READ_SAMPLES * 2)
                if not data:
                    eof = True
                    break
                data = carry + data
                cut = len(data) - len(data) % 2
                carry = data[cut:]
                buf = np.concatenate((buf, np.frombuffer(data[:cut], dtype="<i2")))
            if abort.is_set():
                return
            end = off + len(buf)
            last = eof
            if last:
                b = end
            else:
                b = find_split(buf, off, a + chunk // 2, a + chunk + chunk // 2, a + chunk)
                if b is None:
                    b = a + chunk          # no silence in 1.5 chunks: split on the grid anyway
                    t.forced += 1
            s0 = max(0, a - WARM)
            stop = end if last else b + TAIL
            job = _Job((t.rank, k, next(seq)), t, k, a, b, s0, last,
                       buf[s0 - off:stop - off].tobytes())
            if last:
                exited_cleanly()
                t.total = end
                t.chunks = k + 1
            if not submit(job):
                return
            if last:
                return
            a, k = b, k + 1
            keep = a - WARM - off
            if keep > 0:
                buf = buf[keep:].copy()
                off += keep

    def work() -> None:
        while True:
            job = jobs.get()
            if job.tier is None:
                return
            try:
                if abort.is_set():
                    continue
                pk, head, tags, serial, final = _encode_chunk(job, bitrate_k)
                finished = job.tier.add(job, pk, head, tags, serial, final, abort)
                events.put(("chunk", job.b - job.a))
                if finished:
                    events.put(("tier", job.tier.key))
            except BaseException as exc:  # noqa: BLE001 - handed to the calling thread
                fail(exc)
            finally:
                slots.release()

    producers = [threading.Thread(target=produce, args=(t,), name=f"tier-pcm-{t.key}",
                                  daemon=True) for t in states]
    pool = [threading.Thread(target=work, name=f"tier-enc-{i}", daemon=True)
            for i in range(max(1, workers))]
    for th in producers + pool:
        th.start()
    expected = sum(t.expected for t in states) or 1
    done = finished = 0
    last_report = 0.0
    ok = False
    try:
        while finished < len(states):
            try:
                kind, val = events.get(timeout=1.0)
            except queue.Empty:
                continue
            if kind == "error":
                raise val
            if kind == "tier":
                finished += 1
                continue
            done += val
            now = time.monotonic()
            if on_progress and now - last_report >= PROGRESS_EVERY_S:
                last_report = now
                on_progress(min(done / expected, 1.0))
        ok = True
    finally:
        if not ok:
            abort.set()
            with procs_lock:
                for p in procs:
                    if p.poll() is None:
                        p.kill()
        for _ in pool:
            jobs.put(_Job((float("inf"), 0, next(seq)), None, 0, 0, 0, 0, False, b""))
        for th in producers + pool:
            th.join()
        for t in states:
            t.close_quietly()
            if not ok:
                t.part.unlink(missing_ok=True)
                t.dst.unlink(missing_ok=True)
    out = {}
    for t in states:
        if t.forced:
            logger.warning("[tiers] tier %s: %d of %d splits found no digital silence",
                           t.key, t.forced, t.chunks - 1)
        out[t.key] = {"chunks": t.chunks, "forced_splits": t.forced, "samples": t.total}
    return out
