"""Render artifacts: base.flac, atempo tiers, drift audit, manifest.json.

Built from already-synthesized per-sentence chunks. Every Opus tier is encoded
from ``base.flac`` (never Opus -> Opus) with libopus 24 kbps / 24 kHz mono; long
books are encoded in parallel chunks spliced into one stream per tier
(``services.tier_encode``), short ones with one ffmpeg per tier.
Tier file names are ``audio_{str(float)}.opus`` and the same string is the key
in ``manifest["tiers"]`` (``render_store.reconcile`` relies on that).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import numpy as np
import soundfile as sf

from services import book_processor, ogg_opus, tier_encode
from services.env_config import positive_int

logger = logging.getLogger(__name__)

SR = 24000
BITRATE_K = 24
GAP_DEFAULT_MS = 120
GAP_CHAPTER_MS = 250
GAP_POLICY = book_processor.DEFAULT_GAP_POLICY
SAMPLE_EVERY = 200
TOL_SAMPLED_MS = 60.0
TOL_EDGE_MS = 30.0
# Drift audit on the published tier files (content match, no markers).
AUDIT_HALF_MS = 500.0      # 1x audio compared each side of a boundary
AUDIT_SEARCH_MS = 150.0    # lag search +/- around the manifest's claimed position
AUDIT_FLOOR_DB = -60.0     # envelope floor
AUDIT_FLAT_DB = 3.0        # base window envelope std below this: nothing to align on
AUDIT_FLAT_PROBE = 5       # neighbours tried in place of a flat boundary
AUDIT_MIN_CORR = 0.6       # envelope correlation below this: the audio is not the base's
AUDIT_EDGE_N = 3           # the first/last N boundaries are judged on their median
AUDIT_FACTOR_TOL = 0.01    # actual_factor within 1% of requested (or timeline within TOL_DURATION_MS)
TOL_DURATION_MS = 150.0    # tier length vs base / factor (atempo loses ~15-33 ms)


class RenderError(RuntimeError):
    def __init__(self, message: str, phase: str = "render"):
        super().__init__(message)
        self.phase = phase


class RenderAuditError(RenderError):
    def __init__(self, message: str):
        super().__init__(message, phase="drift_audit")


def tier_workers() -> int:
    """Concurrent tier encodes: ``RENDER_TIER_WORKERS`` or ``min(4, max(1, cpus // 2))``."""
    default = min(4, max(1, (os.cpu_count() or 2) // 2))
    return positive_int("RENDER_TIER_WORKERS", default)


def _run_ordered(items: list, fn, workers: int, on_done=None) -> list:
    """Run ``fn(item)`` for each item on up to ``workers`` threads.

    Results come back in input order whatever the completion order. ``on_done(n)``
    is called from the CALLING thread only, with the running completed count.
    The first failure stops submission, cancels what has not started, waits for
    what is running, then re-raises the failure of the lowest-index item (the one
    the serial loop would have hit first).
    """
    results: list = [None] * len(items)
    errors: dict[int, BaseException] = {}
    nxt = completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        running: dict = {}
        while (nxt < len(items) and not errors) or running:
            while nxt < len(items) and not errors and len(running) < workers:
                running[pool.submit(fn, items[nxt])] = nxt
                nxt += 1
            finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for f in finished:
                i = running.pop(f)
                try:
                    results[i] = f.result()
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    errors[i] = exc
                    continue
                completed += 1
                if on_done and not errors:
                    on_done(completed)
    if errors:
        raise errors[min(errors)]
    return results


def tier_key(t: float) -> str:
    return str(float(t))


# A whole-tier ffmpeg on the one-ffmpeg-per-tier path (a 29 h 1.0 tier takes ~30 min
# at libopus' ~60x real time); past this it is hung, not slow.
FFMPEG_TIMEOUT_S = float(os.environ.get("RENDER_FFMPEG_TIMEOUT_S", str(4 * 3600)))


def _run(cmd: list[str]) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"{cmd[0]} timed out after {FFMPEG_TIMEOUT_S:.0f} s",
                          phase="encode") from exc
    if r.returncode != 0:
        raise RenderError(f"{cmd[0]} failed: {r.stderr.decode(errors='replace')[-400:]}", phase="encode")


def ffprobe_duration_ms(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RenderError(f"ffprobe failed: {r.stderr[-300:]}", phase="encode")
    return float(r.stdout.strip()) * 1000.0


def atempo_filter_chain(factor: float) -> str:
    parts, f = [], factor
    while f > 2.0:
        parts.append(2.0)
        f /= 2.0
    while f < 0.5:
        parts.append(0.5)
        f /= 0.5
    parts.append(f)
    return ",".join(f"atempo={p:.6f}" for p in parts)


def gap_after(chunk: dict, nxt: dict | None) -> int:
    if nxt is None:
        return 0
    text = (chunk.get("text") or "").rstrip()
    if chunk.get("ends_with_comma_or_semicolon") or text.endswith((",", ";")):
        return 0
    if chunk.get("chapter") != nxt.get("chapter"):
        return GAP_CHAPTER_MS
    return GAP_DEFAULT_MS


def build_base(chunks: list[dict], work_dir, *, effective_speed: float = 1.0,
               on_step=None) -> dict:
    """Concatenate chunks losslessly into work_dir/base.flac.

    Returns {path, duration_ms, segments:[{index,start_ms,end_ms,gap_ms,words}]}
    on the 1x timeline. ``on_step(fraction)`` reports 0..1 as chunks are written.
    """
    if effective_speed != 1.0:
        raise RenderError(f"base effective_speed {effective_speed} != 1.0",
                          phase="base_speed_mismatch")
    if not chunks:
        raise RenderError("no chunks", phase="base")
    work = Path(work_dir)
    (work / "chunks").mkdir(parents=True, exist_ok=True)
    segments, pos = [], 0  # pos in samples
    path = work / "base.flac"
    # Stream into base.flac: concatenating every chunk first doubled the book in RAM.
    with sf.SoundFile(path, mode="w", samplerate=SR, channels=1, format="FLAC",
                      subtype="PCM_16") as out:
        for i, c in enumerate(chunks):
            if on_step:
                on_step(i / len(chunks))
            a = np.asarray(c["audio"], dtype=np.float32)
            sf.write(work / "chunks" / f"{c['index']}.flac", a, SR, format="FLAC", subtype="PCM_16")
            gap = gap_after(c, chunks[i + 1] if i + 1 < len(chunks) else None)
            start_ms = pos * 1000.0 / SR
            end_ms = (pos + len(a)) * 1000.0 / SR
            words = [{**w, "start_ms": start_ms + w["start_ms"], "end_ms": start_ms + w["end_ms"]}
                     for w in c.get("words", [])]
            segments.append({"index": c["index"], "start_ms": start_ms, "end_ms": end_ms,
                             "gap_ms": gap, "words": words})
            out.write(a)
            pos += len(a)
            if gap:
                n = int(round(gap * SR / 1000))
                out.write(np.zeros(n, dtype=np.float32))
                pos += n
    if on_step:
        on_step(1.0)
    return {"path": path, "duration_ms": segments[-1]["end_ms"], "segments": segments}


def _encode(src: Path, dst: Path, factor: float) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if factor != 1.0:
        cmd += ["-filter:a", atempo_filter_chain(factor)]
    cmd += ["-ar", str(SR), "-ac", "1", "-c:a", "libopus", "-b:a", f"{BITRATE_K}k", str(dst)]
    _run(cmd)


def _done_marker(path: Path) -> Path:
    return path.with_name(path.name + ".done")


def _reusable(path: Path, base_ms: float) -> bool:
    """A tier encoded from this exact base survives a restart (same length to the ms)."""
    m = _done_marker(path)
    try:
        return path.is_file() and path.stat().st_size > 0 and m.read_text().strip() == f"{base_ms:.3f}"
    except OSError:
        return False


def _encode_tier(base: Path, path: Path, t: float, base_ms: float) -> None:
    """Encode one tier, unless a finished one from this base is already there."""
    if _reusable(path, base_ms):
        return
    _done_marker(path).unlink(missing_ok=True)
    _encode(base, path, t)
    _done_marker(path).write_text(f"{base_ms:.3f}")


def _use_chunked(base_ms: float, segments: list[dict]) -> int:
    """Chunk-encoder pool size, or 0 for the monolithic path (one ffmpeg per tier):
    a single worker, a book shorter than 1.5 chunks, or no sentence gap to split in."""
    workers = tier_encode.encode_workers()
    if workers <= 1 or base_ms < 1.5 * tier_encode.CHUNK_S * 1000:
        return 0
    if not any(s.get("gap_ms", 0) >= 80 for s in segments):
        return 0
    return workers


def build_tiers(base_flac, segments: list[dict], tiers=None, work_dir=None, on_step=None) -> dict:
    """Encode audio_1.0.opus + one file per tier from base.flac.

    Returns {key: {requested_factor, actual_factor, max_drift_ms, duration_ms,
    path, segments}} where segment/word times are 1x times / actual_factor.
    ``on_step(fraction)`` reports 0..1 from the calling thread; the chunked path
    reports as chunks are spliced, so the stall watchdog sees steady progress.
    """
    base = Path(base_flac)
    work = Path(work_dir) if work_dir else base.parent
    tiers = book_processor.TIERS if tiers is None else tiers
    base_ms = ffprobe_duration_ms(base)
    out = {}
    factors = (1.0, *[float(x) for x in tiers if float(x) != 1.0])
    for stale in work.glob("audio_*.opus.part"):  # a crash mid-encode in a resumed tmp dir
        stale.unlink(missing_ok=True)
    # A tmp dir resumed from an older tier set (0.75/1.25/1.75) must not publish orphans.
    current = {f"audio_{tier_key(t)}.opus" for t in factors}
    for old in [*work.glob("audio_*.opus"), *work.glob("audio_*.opus.done")]:
        if old.name.removesuffix(".done") not in current:
            old.unlink(missing_ok=True)

    def _entry(t: float, path: Path) -> dict:
        if t == 1.0:
            actual = 1.0
        else:
            actual = base_ms / ffprobe_duration_ms(path)
        return {
            "requested_factor": t, "actual_factor": actual, "max_drift_ms": 0.0,
            "duration_ms": ffprobe_duration_ms(path), "path": path,
            "segments": [{**s, "start_ms": s["start_ms"] / actual, "end_ms": s["end_ms"] / actual,
                          "words": [{**w, "start_ms": w["start_ms"] / actual,
                                     "end_ms": w["end_ms"] / actual} for w in s["words"]]}
                         for s in segments],
        }

    workers = _use_chunked(base_ms, segments)
    if workers:
        unique: dict[str, float] = {}
        for t in factors:
            unique.setdefault(tier_key(t), t)
        if on_step:
            on_step(0.0)
        for k in unique:  # the chunked path re-encodes; a restart marker would be stale
            _done_marker(work / f"audio_{k}.opus").unlink(missing_ok=True)
        try:
            with sf.SoundFile(base) as f:
                base_samples = f.frames
            tier_encode.encode_tiers(
                base, [(k, atempo_filter_chain(t) if t != 1.0 else None, work / f"audio_{k}.opus")
                       for k, t in unique.items()],
                base_samples=base_samples, factors=unique, workers=workers,
                bitrate_k=BITRATE_K,
                on_progress=(lambda f: on_step(0.99 * f)) if on_step else None)
        except (tier_encode.ChunkEncodeError, ogg_opus.OggError, OSError,
                subprocess.SubprocessError) as exc:
            raise RenderError(f"chunked tier encode failed: {exc}", phase="encode") from exc
        for key, t in unique.items():
            out[key] = _entry(t, work / f"audio_{key}.opus")
        if on_step:
            on_step(1.0)
        return out

    workers = tier_workers()
    if workers <= 1 or len(factors) <= 1:
        for done, t in enumerate(factors):
            key = tier_key(t)
            path = work / f"audio_{key}.opus"
            if on_step:
                on_step(done / len(factors))
            _encode_tier(base, path, t, base_ms)
            out[key] = _entry(t, path)
        if on_step:
            on_step(1.0)
        return out

    # Each tier writes only work/audio_<key>.opus, so tiers cannot clobber each
    # other as long as keys are unique: collapse repeated factors (the serial loop
    # would just overwrite the same dict entry) so two threads never share a file.
    unique: dict[str, float] = {}
    for t in factors:
        unique.setdefault(tier_key(t), t)
    items = list(unique.items())

    def _one(item):
        key, t = item
        path = work / f"audio_{key}.opus"
        try:
            _encode_tier(base, path, t, base_ms)
            return _entry(t, path)
        except BaseException:
            path.unlink(missing_ok=True)
            _done_marker(path).unlink(missing_ok=True)
            raise

    if on_step:
        on_step(0.0)
    entries = _run_ordered(items, _one, workers,
                           (lambda n: on_step(n / len(items))) if on_step else None)
    for (key, _t), entry in zip(items, entries):
        out[key] = entry
    if on_step:
        on_step(1.0)
    return out


def _boundaries(segments: list[dict]) -> list[float]:
    """Gap-centre times (1x ms) of the boundaries with a real pause (>= 80 ms)."""
    return [s["end_ms"] + s["gap_ms"] / 2 for s in segments if s["gap_ms"] >= 80]


def _read_window(f, lo: int, hi: int) -> np.ndarray:
    """Samples ``[lo, hi)`` of an open SoundFile, zero-padded outside the file.

    Only this window is decoded, so memory stays flat however long the book is
    (whole-book arrays were ~80 GB for a 29 h book)."""
    out = np.zeros(max(0, hi - lo), dtype=np.float32)
    a, b = max(0, lo), min(f.frames, hi)
    if b > a:
        f.seek(a)
        got = f.read(b - a, dtype="float32", always_2d=True)[:, 0]
        out[a - lo:a - lo + len(got)] = got
    return out


def _envelope(f, lo_ms: float, hi_ms: float) -> tuple[np.ndarray, np.ndarray]:
    """dB RMS envelope (4 ms window, 1 ms hop) of ``f`` over [lo_ms, hi_ms]: (times_ms, db)."""
    sr = f.samplerate
    hop, win = sr // 1000, 4 * sr // 1000
    lo, hi = int(lo_ms * sr / 1000), int(hi_ms * sr / 1000)
    x = _read_window(f, lo, hi).astype(np.float64)
    n = (len(x) - win) // hop + 1
    if n <= 0:
        return np.zeros(0), np.zeros(0)
    c = np.concatenate([[0.0], np.cumsum(x * x)])
    starts = np.arange(n) * hop
    rms = np.sqrt(np.maximum(c[starts + win] - c[starts], 0.0) / win)
    db = np.maximum(20 * np.log10(np.maximum(rms, 1e-9)), AUDIT_FLOOR_DB)
    t = (lo + starts + win / 2) / sr * 1000
    return t, db


def _base_window(fb, c_ms: float):
    """1x envelope around a boundary, or None when it is flat (nothing to align on)."""
    t, db = _envelope(fb, c_ms - AUDIT_HALF_MS - 10, c_ms + AUDIT_HALF_MS + 10)
    inside = (t >= c_ms - AUDIT_HALF_MS) & (t <= c_ms + AUDIT_HALF_MS)
    if len(t) == 0 or db[inside].std() < AUDIT_FLAT_DB:
        return None
    return t, db


def _content_drift(base_env, ft, c_ms: float, factor: float, claimed_ms: float):
    """Where the 1x content around ``c_ms`` really sits in the tier file.

    The base envelope, time-scaled by ``1/factor``, is cross-correlated with the tier
    envelope around ``claimed_ms`` (lags +/-AUDIT_SEARCH_MS, parabolic refinement).
    Returns (drift_ms, correlation)."""
    tb, eb = base_env
    half = AUDIT_HALF_MS / factor
    v = np.arange(-half, half, 1.0)
    ref = np.interp(c_ms + v * factor, tb, eb)
    ref = ref - ref.mean()
    tt, et = _envelope(ft, claimed_ms - half - AUDIT_SEARCH_MS - 10,
                       claimed_ms + half + AUDIT_SEARCH_MS + 10)
    lags = np.arange(-AUDIT_SEARCH_MS, AUDIT_SEARCH_MS + 1, 1.0)
    if len(tt) == 0:
        return float("nan"), 0.0
    y = np.interp(claimed_ms + lags[:, None] + v[None, :], tt, et)
    y = y - y.mean(axis=1, keepdims=True)
    den = np.linalg.norm(y, axis=1) * np.linalg.norm(ref)
    sc = np.where(den > 1e-9, (y @ ref) / np.maximum(den, 1e-12), 0.0)
    k = int(np.argmax(sc))
    d = float(lags[k])
    if 0 < k < len(sc) - 1:
        a, b, c = sc[k - 1], sc[k], sc[k + 1]
        den2 = a - 2 * b + c
        if den2 < 0:
            d += 0.5 * (a - c) / den2
    return d, float(sc[k])


def _audit_points(fb, centers: np.ndarray) -> tuple[dict, int]:
    """Boundaries to check: every SAMPLE_EVERY-th plus the first and last
    AUDIT_EDGE_N, each with its 1x envelope. A flat (silent) window is replaced by
    the next boundary with audible content (up to AUDIT_FLAT_PROBE tries); the ones
    that stay flat are counted, never silently passed."""
    n = len(centers)
    wanted = sorted(set(range(0, n, SAMPLE_EVERY)) | set(range(min(AUDIT_EDGE_N, n)))
                    | set(range(max(0, n - AUDIT_EDGE_N), n)))
    points: dict[int, tuple] = {}
    flat = 0
    for i in wanted:
        step = -1 if i >= n - AUDIT_EDGE_N else 1  # end boundaries probe inwards
        for j in (i + step * k for k in range(AUDIT_FLAT_PROBE + 1)):
            if not 0 <= j < n or j in points:
                continue
            env = _base_window(fb, centers[j])
            if env is not None:
                points[j] = env
                break
        else:
            flat += 1
    return dict(sorted(points.items())), flat


def _audit_tier(key: str, info: dict, path: Path, base_ms: float, centers: np.ndarray,
                points: dict) -> float:
    """Audit one published tier file against the base; returns its max |drift| (ms)."""
    req, act = float(info["requested_factor"]), float(info["actual_factor"])
    # atempo gains or drops a few tens of ms at the end of a file, which moves
    # actual_factor by >1% on a 1-2 sentence book; only a factor that is both
    # relatively AND absolutely (timeline length) off is wrong.
    if (abs(act / req - 1) > AUDIT_FACTOR_TOL
            and abs(base_ms / act - base_ms / req) > TOL_DURATION_MS):
        raise RenderAuditError(
            f"tier {key}: actual_factor {act:.4f} is more than "
            f"{AUDIT_FACTOR_TOL:.0%} off requested {req} "
            f"({abs(base_ms / act - base_ms / req):.0f} ms of timeline)")
    try:
        ogg_opus.check_stream(path)  # every page: CRC, sequence, EOS (no decode, ~1 s for 29 h)
    except (OSError, ogg_opus.OggError) as exc:
        raise RenderAuditError(f"tier {key}: {path.name} is corrupt: {exc}") from exc
    try:
        ft = sf.SoundFile(path)
    except Exception as exc:  # noqa: BLE001 - any decode failure is an audit failure
        raise RenderAuditError(f"tier {key}: cannot read {path.name}: {exc}") from exc
    with ft:
        tier_ms = ft.frames / ft.samplerate * 1000
        want = base_ms / req
        if abs(tier_ms - want) > TOL_DURATION_MS:
            raise RenderAuditError(
                f"tier {key}: {path.name} lasts {tier_ms:.0f} ms, expected {want:.0f} ms "
                f"(base / {req}); truncated or the wrong tier")
        drifts: dict[int, float] = {}
        try:
            for i, env in points.items():
                d, corr = _content_drift(env, ft, centers[i], req, centers[i] / act)
                if not np.isfinite(d) or corr < AUDIT_MIN_CORR:
                    raise RenderAuditError(
                        f"tier {key}: audio does not match the base at boundary {i} "
                        f"(correlation {corr:.2f} < {AUDIT_MIN_CORR})")
                if abs(d) > TOL_SAMPLED_MS:
                    raise RenderAuditError(
                        f"tier {key}: drift {d:+.1f} ms at boundary {i} exceeds "
                        f"{TOL_SAMPLED_MS:.0f} ms")
                drifts[i] = d
        except sf.LibsndfileError as exc:
            raise RenderAuditError(f"tier {key}: cannot decode {path.name}: {exc}") from exc
    if not drifts:
        return 0.0
    idx = list(drifts)
    # The ends are judged on the median of a few boundaries: one envelope match
    # can be a few ms off on its own, while a real offset moves all of them.
    for name, group in (("first", idx[:AUDIT_EDGE_N]), ("last", idx[-AUDIT_EDGE_N:])):
        m = float(np.median([drifts[i] for i in group]))
        if abs(m) > TOL_EDGE_MS:
            raise RenderAuditError(
                f"tier {key}: drift {m:+.1f} ms (median of the {name} {len(group)} "
                f"boundaries) exceeds {TOL_EDGE_MS:.0f} ms")
    return float(max(abs(d) for d in drifts.values()))


def drift_audit(base_flac, segments: list[dict], tier_results: dict, work_dir=None,
                on_step=None) -> dict:
    """Audit the real tier files; fills max_drift_ms per tier, raises RenderAuditError.

    Every Ogg page of each tier file is checked (CRC, sequence, EOS), then the
    file is decoded only around sampled sentence boundaries (seeked windows,
    flat memory). Its content there is matched against the 1x base
    time-scaled by the tier's factor, and must sit where the manifest's timeline
    (1x time / actual_factor) says it does. The file must also decode and last
    base / factor. ``on_step(fraction)`` reports 0..1 as tiers finish.
    """
    base = Path(base_flac)
    work = Path(work_dir) if work_dir else base.parent
    centers = np.array(_boundaries(segments))
    with sf.SoundFile(base) as fb:
        base_ms = fb.frames / fb.samplerate * 1000
        points, flat = _audit_points(fb, centers) if len(centers) else ({}, 0)
    if len(centers) and not points:
        raise RenderAuditError(
            f"no audible content near any of the {flat} sampled boundaries; nothing to audit")
    if flat:
        logger.warning("[audit] %d sampled boundaries skipped: flat (silent) with no audible "
                       "neighbour; %d checked", flat, len(points))
    items = list(tier_results.items())

    def _one(item):
        key, info = item
        path = Path(info.get("path") or work / f"audio_{key}.opus")
        return _audit_tier(key, info, path, base_ms, centers, points)

    if on_step:
        on_step(0.0)
    drifts = _run_ordered(items, _one, tier_workers(),
                          (lambda n: on_step(n / len(items))) if on_step else None)
    report = {}
    for (key, info), d in zip(items, drifts):
        info["max_drift_ms"] = d
        report[key] = d
    if on_step:
        on_step(1.0)
    return report


def _timing_quality(segments: list[dict]) -> str:
    srcs = {w.get("timing_source") for s in segments for w in s["words"]}
    if "estimated" in srcs:
        return "estimated"
    if "aligned" in srcs:
        return "aligned"
    return "exact"


def write_manifest(work_dir, *, base_segments: list[dict], tier_results: dict, duration_ms: float,
                   voice: str, voice_file_sha256: str, fingerprint: str,
                   base_effective_speed: float = 1.0, mixed_backends: bool = False,
                   gap_policy: str = GAP_POLICY, sample_rate: int = SR) -> dict:
    if base_effective_speed != 1.0:
        raise RenderError(f"base effective_speed {base_effective_speed} != 1.0",
                          phase="base_speed_mismatch")
    tiers = {}
    for key, info in tier_results.items():
        tiers[key] = {
            "requested_factor": info["requested_factor"],
            "actual_factor": info["actual_factor"],
            "max_drift_ms": info["max_drift_ms"],
            "segments": [{"index": s["index"], "start_ms": s["start_ms"], "end_ms": s["end_ms"],
                          "gap_ms": s["gap_ms"],
                          "words": [{"start_ms": w["start_ms"], "end_ms": w["end_ms"],
                                     "box": w.get("box"), "timing_source": w.get("timing_source")}
                                    for w in s["words"]]} for s in info["segments"]],
        }
    manifest = {
        "schema": 1, "duration_ms": duration_ms, "base_effective_speed": base_effective_speed,
        "voice": voice, "voice_file_sha256": voice_file_sha256, "fingerprint": fingerprint,
        "container": "ogg-opus", "sample_rate": sample_rate,
        "timing_quality": _timing_quality(base_segments), "mixed_backends": mixed_backends,
        "gap_policy": gap_policy, "tiers": tiers,
    }
    (Path(work_dir) / "manifest.json").write_text(json.dumps(manifest))
    return manifest
