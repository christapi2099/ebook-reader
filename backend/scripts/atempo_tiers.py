"""Spike: produce extra speed tiers by audio-domain time-stretching (ffmpeg
atempo, a WSOLA-family algorithm) instead of Kokoro's native speed= param.

This is the technique Speechify/Natural Reader/YouTube playback-rate actually
use: synthesize once at a moderate rate, then stretch the WAVEFORM uniformly
rather than asking the TTS model's duration predictor to produce very short
frames (which is what caused the taper/whisper artifact observed at Kokoro
speed=2.5/3.0 in this spike).

Reads an existing tier written by precompile_sample.py (sample_<tag>.opus +
manifest_<tag>.json) and, for each --targets value, writes:
  scripts/output/sample_atempo_<target>.opus
  scripts/output/manifest_atempo_<target>.json
then merges these into scripts/output/tiers.json so the test page can list
native-speed tiers next to atempo tiers for direct comparison.

Usage:
    cd backend && uv run python scripts/atempo_tiers.py --base 1 --targets 1.5,2.0,2.5,3.0
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"


def atempo_filter_chain(factor: float) -> str:
    """ffmpeg's atempo filter only accepts [0.5, 2.0] per instance; chain for more."""
    parts = []
    f = factor
    while f > 2.0:
        parts.append(2.0)
        f /= 2.0
    while f < 0.5:
        parts.append(0.5)
        f /= 0.5
    parts.append(f)
    return ",".join(f"atempo={p:.6f}" for p in parts)


def ffprobe_duration_ms(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip()) * 1000.0


def build_tier(base_tag: str, target_speed: float, bitrate: int) -> dict:
    base_manifest_path = OUTPUT_DIR / f"manifest_{base_tag}.json"
    base_audio_path = OUTPUT_DIR / f"sample_{base_tag}.opus"
    if not base_manifest_path.exists() or not base_audio_path.exists():
        raise SystemExit(f"missing base tier files: {base_manifest_path}, {base_audio_path} "
                          f"(run precompile_sample.py with --speeds including {base_tag!r} first)")

    base_manifest = json.loads(base_manifest_path.read_text())
    base_effective_speed = base_manifest["effective_speed"]
    factor = target_speed / base_effective_speed

    tag = f"atempo_{target_speed:g}"
    out_path = OUTPUT_DIR / f"sample_{tag}.opus"
    filter_chain = atempo_filter_chain(factor)

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(base_audio_path),
         "-filter:a", filter_chain,
         "-ar", "24000", "-ac", "1",
         "-c:a", "libopus", "-b:a", f"{bitrate}k",
         str(out_path)],
        capture_output=True, check=True,
    )

    base_duration_ms = ffprobe_duration_ms(base_audio_path)
    new_duration_ms = ffprobe_duration_ms(out_path)
    # Measure the actual scale ffmpeg produced rather than trusting `factor`
    # exactly — atempo's chained filters and frame rounding mean real output
    # duration can differ from base_duration/factor by a few ms, and that
    # error compounds badly on word-level timestamps if left uncorrected.
    scale = new_duration_ms / base_duration_ms

    segments = []
    for seg in base_manifest["segments"]:
        segments.append({
            **seg,
            "start_ms": seg["start_ms"] * scale,
            "end_ms": seg["end_ms"] * scale,
            "words": [
                {**w, "start_ms": w["start_ms"] * scale, "end_ms": w["end_ms"] * scale}
                for w in seg["words"]
            ],
        })

    manifest_name = f"manifest_{tag}.json"
    manifest = {
        "requested_speed": target_speed,
        "effective_speed": target_speed,
        "method": f"atempo (base={base_tag}@{base_effective_speed}x, factor={factor:.4f})",
        "sample_rate": base_manifest["sample_rate"],
        "audio_url": out_path.name,
        "duration_ms": new_duration_ms,
        "segments": segments,
    }
    (OUTPUT_DIR / manifest_name).write_text(json.dumps(manifest, indent=2))

    print(f"  atempo {target_speed}x (chain={filter_chain}): -> {out_path.name} "
          f"({out_path.stat().st_size}B), {new_duration_ms:.0f}ms "
          f"(measured scale {scale:.4f} vs requested factor {factor:.4f})")

    return {
        "tag": tag,
        "requested_speed": target_speed,
        "effective_speed": target_speed,
        "method": "atempo",
        "audio_url": out_path.name,
        "manifest_url": manifest_name,
        "duration_ms": new_duration_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="1", help="tag of the already-synthesized base tier to stretch")
    parser.add_argument("--targets", default="1.5,2.0,2.5,3.0",
                         help="comma-separated target speeds (relative to normal 1x)")
    parser.add_argument("--bitrate", type=int, default=24)
    args = parser.parse_args()

    targets = [float(t) for t in args.targets.split(",") if t.strip()]

    tiers_path = OUTPUT_DIR / "tiers.json"
    if not tiers_path.exists():
        raise SystemExit(f"{tiers_path} not found — run precompile_sample.py first")
    tiers_index = json.loads(tiers_path.read_text())

    # Mark existing native tiers explicitly so the test page can label method.
    for t in tiers_index["tiers"]:
        t.setdefault("method", "native")

    print(f"stretching base tier {args.base!r} to targets {targets}")
    new_entries = [build_tier(args.base, target, args.bitrate) for target in targets]

    # Replace any prior atempo entries for the same tags, keep everything else.
    existing_tags = {e["tag"] for e in new_entries}
    tiers_index["tiers"] = [t for t in tiers_index["tiers"] if t["tag"] not in existing_tags] + new_entries
    tiers_index["tiers"].sort(key=lambda t: t["requested_speed"])

    tiers_path.write_text(json.dumps(tiers_index, indent=2))
    print(f"\nupdated {tiers_path} ({len(tiers_index['tiers'])} total tiers)")


if __name__ == "__main__":
    main()
