"""WebM (Opus) copies of the published Ogg Opus tier files.

Firefox on Linux mishandles an Ogg Opus file longer than 2**31 / 48000 s (12 h 25 m):
metadata takes seconds, seeks are slow and the reported duration is cut at 44,739 s
(Bug 1810378, measured on Firefox 158). The same packets in a WebM container play
correctly, so every tier gets an ``audio_{tier}.webm`` next to its ``audio_{tier}.opus``.
It is a stream copy (``ffmpeg -c copy``, no re-encode): the audio is bit-identical and
the timeline the drift audit checked on the Ogg file still holds.

The Ogg files stay (the audit and old clients read them). A generation offers WebM
when *every* Ogg tier has a WebM sibling (``available_formats``), so a half-converted
generation never serves a mix.
"""
from __future__ import annotations

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from services.env_config import positive_int
from services.render_artifacts import ffprobe_duration_ms

logger = logging.getLogger(__name__)

FORMAT_WEBM = "webm"
FORMAT_OGG = "opus"
# A remux is a sequential copy (~11 s per 150 MB); this only guards a hung ffmpeg.
REMUX_TIMEOUT_S = 1800
# The two files are the same packets, so lengths differ by at most a muxer rounding.
DURATION_TOLERANCE_MS = 50.0


class RemuxError(RuntimeError):
    pass


def enabled() -> bool:
    """``RENDER_WEBM=0`` turns the WebM copy off for new renders."""
    return os.environ.get("RENDER_WEBM", "1").strip().lower() not in ("0", "false", "no", "off")


def workers() -> int:
    return positive_int("RENDER_WEBM_WORKERS", 4)


def webm_path(opus: Path) -> Path:
    return Path(opus).with_suffix(".webm")


def remux_tier(opus: Path) -> Path:
    """Write ``opus`` as ``.webm`` beside it (stream copy, atomic) and return the path.

    The copy is only renamed into place after ffprobe agrees with the source on its
    duration; any failure removes the partial file and raises ``RemuxError``.
    """
    opus = Path(opus)
    dst = webm_path(opus)
    part = dst.with_name(dst.name + ".part")
    part.unlink(missing_ok=True)
    try:
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(opus), "-map", "0:a:0",
             "-c", "copy", "-map_metadata", "-1", "-f", "webm", str(part)],
            capture_output=True, text=True, timeout=REMUX_TIMEOUT_S)
        if r.returncode != 0:
            raise RemuxError(f"ffmpeg remux of {opus.name} failed: {r.stderr[-300:]}")
        src_ms, out_ms = ffprobe_duration_ms(opus), ffprobe_duration_ms(part)
        if abs(src_ms - out_ms) > DURATION_TOLERANCE_MS:
            raise RemuxError(f"{opus.name}: webm lasts {out_ms:.0f} ms, ogg {src_ms:.0f} ms")
        os.replace(part, dst)
    except (subprocess.SubprocessError, OSError) as exc:
        raise RemuxError(f"remux of {opus.name} failed: {exc}") from exc
    finally:
        part.unlink(missing_ok=True)
    return dst


def remux_tiers(opus_files: list[Path], max_workers: int | None = None) -> list[Path]:
    """Remux every file (a small pool); on the first failure the copies made by this
    call are removed and ``RemuxError`` is raised, so a generation is never half-WebM."""
    files = [Path(f) for f in opus_files]
    done: list[Path] = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers or workers(), len(files) or 1))) as pool:
            for out in pool.map(remux_tier, files):
                done.append(out)
    except RemuxError:
        for f in files:
            webm_path(f).unlink(missing_ok=True)
        raise
    return done


def available_formats(gen_dir: Path) -> tuple[str, ...]:
    """Formats a generation can serve, best first: ``("webm", "opus")`` when every
    Ogg tier has a non-empty WebM sibling, else ``("opus",)``."""
    gen_dir = Path(gen_dir)
    opus = sorted(gen_dir.glob("audio_*.opus"))
    if opus and all(webm_path(f).is_file() and webm_path(f).stat().st_size > 0 for f in opus):
        return (FORMAT_WEBM, FORMAT_OGG)
    return (FORMAT_OGG,)
