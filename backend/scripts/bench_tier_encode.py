"""Time the tier encode and drift audit on a published render, and check the chunked
encode against the render's own (monolithic) tier files.

Usage (from ``backend/``):

    uv run python scripts/bench_tier_encode.py renders/<book>/<gen> --workers 1,8,12
    uv run python scripts/bench_tier_encode.py renders/<book>/<gen> --workers 12 --compare

The render directory is only read. Tiers are re-encoded from its ``base.flac`` into a
temporary directory (removed afterwards unless ``--keep DIR``). ``--workers 1`` is the
one-ffmpeg-per-tier path (``RENDER_TIER_WORKERS`` of them at once); more uses the chunked
pool. ``--compare`` also checks every re-encoded tier the render already has: packet count,
final granule, ffprobe duration and decoded length must equal the published file, and the
decoded audio's error against the encoder input (the atempo PCM) is printed for both.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from services import ogg_opus, render_artifacts as ra, tier_encode as te  # noqa: E402
from services.render_manifest import tier_segments  # noqa: E402

SR = 24000


def _pcm(cmd: list[str]) -> np.ndarray:
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, "<i2").astype(np.float64) / 32768


def decode(path: Path) -> np.ndarray:
    return _pcm(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "s16le",
                 "-ar", str(SR), "-ac", "1", "pipe:1"])


def source(base: Path, factor: float) -> np.ndarray:
    chain = ["-filter:a", ra.atempo_filter_chain(factor)] if factor != 1.0 else []
    return _pcm(["ffmpeg", "-nostdin", "-v", "error", "-i", str(base), *chain,
                 "-ar", str(SR), "-ac", "1", "-f", "s16le", "pipe:1"])


def compare(render: Path, out: Path, res: dict) -> bool:
    ok = True
    base = render / "base.flac"
    for key, info in res.items():
        pub = render / f"audio_{key}.opus"
        if not pub.exists():
            continue
        new = out / f"audio_{key}.opus"
        _, pp, gp = ogg_opus.read_packets(pub.read_bytes())
        _, pn, gn = ogg_opus.read_packets(new.read_bytes())
        src, xp, xn = source(base, info["requested_factor"]), decode(pub), decode(new)
        same = (len(pp) == len(pn) and gp == gn and len(xp) == len(xn)
                and ra.ffprobe_duration_ms(pub) == ra.ffprobe_duration_ms(new))
        n = min(len(src), len(xp), len(xn))

        def snr(x):
            return 10 * np.log10(np.sum(src[:n] ** 2) / np.sum((x[:n] - src[:n]) ** 2))

        print(f"  tier {key}: packets {len(pn)}/{len(pp)} granule {gn}/{gp} decoded {len(xn)}/{len(xp)} "
              f"-> {'SAME timeline' if same else 'DIFFERENT'}; SNR vs encoder input: "
              f"new {snr(xn):.2f} dB, published {snr(xp):.2f} dB")
        ok &= same
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("render", type=Path, help="a published generation directory")
    ap.add_argument("--workers", default="1,8", help="comma list of RENDER_ENCODE_WORKERS")
    ap.add_argument("--tiers", default=",".join(str(t) for t in ra.book_processor.TIERS))
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--keep", type=Path, default=None, metavar="DIR")
    args = ap.parse_args(argv)
    man = json.loads((args.render / "manifest.json").read_text())
    segs = tier_segments(man, "1.0")  # either manifest schema
    base = args.render / "base.flac"
    tiers = tuple(float(t) for t in args.tiers.split(","))
    print(f"{args.render}: {man['duration_ms'] / 60000:.1f} min, tiers 1.0+{tiers}, "
          f"{len(ra._boundaries(segs))} boundaries")
    ok = True
    for w in (int(x) for x in args.workers.split(",")):
        os.environ["RENDER_ENCODE_WORKERS"] = str(w)
        out = Path(tempfile.mkdtemp(prefix="bench-tiers-"))
        try:
            t0, c0 = time.time(), os.times()
            res = ra.build_tiers(base, segs, tiers, out)
            c1, t1 = os.times(), time.time()
            report = ra.drift_audit(base, segs, res, out)
            t2 = time.time()
            cpu = c1.children_user + c1.children_system - c0.children_user - c0.children_system
            mode = "chunked" if ra._use_chunked(ra.ffprobe_duration_ms(base), segs) else "monolithic"
            print(f"workers={w} ({mode}): encode {t1 - t0:.2f} s wall, {cpu:.1f} s ffmpeg CPU; "
                  f"audit {t2 - t1:.2f} s; max drift "
                  + ", ".join(f"{k} {v:.1f}" for k, v in report.items()) + " ms")
            if args.compare:
                ok &= compare(args.render, out, res)
            if args.keep:
                dst = args.keep / f"w{w}"
                dst.mkdir(parents=True, exist_ok=True)
                for p in out.glob("audio_*.opus"):
                    shutil.copy2(p, dst / p.name)
        finally:
            shutil.rmtree(out, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
