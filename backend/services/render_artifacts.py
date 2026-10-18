"""Render artifacts: base.flac, atempo tiers, drift audit, manifest.json.

Built from already-synthesized per-sentence chunks. Every Opus tier is encoded
from ``base.flac`` (never Opus -> Opus) with libopus 24 kbps / 24 kHz mono.
Tier file names are ``audio_{str(float)}.opus`` and the same string is the key
in ``manifest["tiers"]`` (``render_store.reconcile`` relies on that).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from services import book_processor

SR = 24000
BITRATE_K = 24
GAP_DEFAULT_MS = 120
GAP_CHAPTER_MS = 250
GAP_POLICY = book_processor.DEFAULT_GAP_POLICY
SAMPLE_EVERY = 200
TOL_SAMPLED_MS = 60.0
TOL_EDGE_MS = 30.0
MARK_HZ = 5000.0
MARK_MS = 40.0


class RenderError(RuntimeError):
    def __init__(self, message: str, phase: str = "render"):
        super().__init__(message)
        self.phase = phase


class RenderAuditError(RenderError):
    def __init__(self, message: str):
        super().__init__(message, phase="drift_audit")


def tier_key(t: float) -> str:
    return str(float(t))


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True)
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


def build_base(chunks: list[dict], work_dir, *, effective_speed: float = 1.0) -> dict:
    """Concatenate chunks losslessly into work_dir/base.flac.

    Returns {path, duration_ms, segments:[{index,start_ms,end_ms,gap_ms,words}]}
    on the 1x timeline.
    """
    if effective_speed != 1.0:
        raise RenderError(f"base effective_speed {effective_speed} != 1.0",
                          phase="base_speed_mismatch")
    if not chunks:
        raise RenderError("no chunks", phase="base")
    work = Path(work_dir)
    (work / "chunks").mkdir(parents=True, exist_ok=True)
    pieces, segments, pos = [], [], 0  # pos in samples
    for i, c in enumerate(chunks):
        a = np.asarray(c["audio"], dtype=np.float32)
        sf.write(work / "chunks" / f"{c['index']}.flac", a, SR, format="FLAC", subtype="PCM_16")
        gap = gap_after(c, chunks[i + 1] if i + 1 < len(chunks) else None)
        start_ms = pos * 1000.0 / SR
        end_ms = (pos + len(a)) * 1000.0 / SR
        words = [{**w, "start_ms": start_ms + w["start_ms"], "end_ms": start_ms + w["end_ms"]}
                 for w in c.get("words", [])]
        segments.append({"index": c["index"], "start_ms": start_ms, "end_ms": end_ms,
                         "gap_ms": gap, "words": words})
        pieces.append(a)
        pos += len(a)
        if gap:
            n = int(round(gap * SR / 1000))
            pieces.append(np.zeros(n, dtype=np.float32))
            pos += n
    path = work / "base.flac"
    sf.write(path, np.concatenate(pieces), SR, format="FLAC", subtype="PCM_16")
    return {"path": path, "duration_ms": segments[-1]["end_ms"], "segments": segments}


def _encode(src: Path, dst: Path, factor: float) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if factor != 1.0:
        cmd += ["-filter:a", atempo_filter_chain(factor)]
    cmd += ["-ar", str(SR), "-ac", "1", "-c:a", "libopus", "-b:a", f"{BITRATE_K}k", str(dst)]
    _run(cmd)


def build_tiers(base_flac, segments: list[dict], tiers=None, work_dir=None) -> dict:
    """Encode audio_1.0.opus + one file per tier from base.flac.

    Returns {key: {requested_factor, actual_factor, max_drift_ms, duration_ms,
    path, segments}} where segment/word times are 1x times / actual_factor.
    """
    base = Path(base_flac)
    work = Path(work_dir) if work_dir else base.parent
    tiers = book_processor.TIERS if tiers is None else tiers
    base_ms = ffprobe_duration_ms(base)
    out = {}
    for t in (1.0, *[float(x) for x in tiers if float(x) != 1.0]):
        key = tier_key(t)
        path = work / f"audio_{key}.opus"
        _encode(base, path, t)
        if t == 1.0:
            actual = 1.0
        else:
            actual = base_ms / ffprobe_duration_ms(path)
        out[key] = {
            "requested_factor": t, "actual_factor": actual, "max_drift_ms": 0.0,
            "duration_ms": ffprobe_duration_ms(path), "path": path,
            "segments": [{**s, "start_ms": s["start_ms"] / actual, "end_ms": s["end_ms"] / actual,
                          "words": [{**w, "start_ms": w["start_ms"] / actual,
                                     "end_ms": w["end_ms"] / actual} for w in s["words"]]}
                         for s in segments],
        }
    return out


def _boundaries(segments: list[dict]) -> list[float]:
    """Gap-center times (1x ms) of boundaries that have a gap to hold a marker."""
    return [s["end_ms"] + s["gap_ms"] / 2 for s in segments if s["gap_ms"] >= 80]


def _detect(y: np.ndarray, expected_ms: np.ndarray, half_ms: float = 150.0) -> np.ndarray:
    t = np.arange(len(y)) / SR
    z = y * np.exp(-2j * np.pi * MARK_HZ * t)
    k = int(SR * 0.004)
    env = np.abs(np.convolve(z, np.ones(k) / k, mode="same"))
    found = []
    for e in expected_ms:
        lo = int(max(0, e - half_ms) / 1000 * SR)
        hi = int(min(len(y) / SR * 1000, e + half_ms) / 1000 * SR)
        w = env[lo:hi]
        if len(w) < 3:
            raise RenderAuditError(f"marker window empty near {e:.0f} ms")
        pk = w.max()
        a0 = int(np.argmax(w))
        if pk < 6 * np.median(w) + 0.01 or a0 in (0, len(w) - 1):
            raise RenderAuditError(f"marker not detected near {e:.0f} ms")
        m = w > 0.5 * pk
        l = r = a0
        while l > 0 and m[l - 1]:
            l -= 1
        while r < len(w) - 1 and m[r + 1]:
            r += 1
        idx = np.arange(l, r + 1)
        found.append((lo + float((idx * w[idx]).sum() / w[idx].sum())) / SR * 1000)
    return np.array(found)


def drift_audit(base_flac, segments: list[dict], tier_results: dict, work_dir=None) -> dict:
    """Marker-tone drift audit; fills max_drift_ms per tier, raises RenderAuditError."""
    base = Path(base_flac)
    work = Path(work_dir) if work_dir else base.parent
    centers = np.array(_boundaries(segments))
    if len(centers) == 0:
        return {}
    pick = sorted(set(range(0, len(centers), SAMPLE_EVERY)) | {0, len(centers) - 1})
    x, _ = sf.read(base, dtype="float32")
    n = int(SR * MARK_MS / 1000)
    tone = (0.6 * np.hanning(n) * np.sin(2 * np.pi * MARK_HZ * np.arange(n) / SR)).astype(np.float32)
    for i in pick:
        c = int(centers[i] / 1000 * SR)
        x[c - n // 2:c - n // 2 + n] += tone
    mbase = work / "audit_markers.flac"
    sf.write(mbase, np.clip(x, -1, 1), SR, format="FLAC", subtype="PCM_16")
    exp = centers[pick]
    report = {}
    try:
        for key, info in tier_results.items():
            opus = work / f"audit_{key}.opus"
            wav = work / f"audit_{key}.wav"
            _encode(mbase, opus, info["requested_factor"])
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(opus), "-ar", str(SR), "-ac", "1", str(wav)])
            y, _ = sf.read(wav, dtype="float32")
            lin = exp / info["actual_factor"]
            drift = _detect(y, lin) - lin
            for j, d in enumerate(drift):
                edge = pick[j] in (0, len(centers) - 1)
                tol = TOL_EDGE_MS if edge else TOL_SAMPLED_MS
                if abs(d) > tol:
                    raise RenderAuditError(
                        f"tier {key}: drift {d:+.1f} ms at boundary {pick[j]} exceeds {tol:.0f} ms")
            info["max_drift_ms"] = float(np.abs(drift).max())
            report[key] = info["max_drift_ms"]
            opus.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)
    finally:
        mbase.unlink(missing_ok=True)
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
