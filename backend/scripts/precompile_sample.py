"""Spike: precompile one chapter's audio to Opus + a sentence/word sync manifest,
with every non-1x speed tier derived by audio-domain time-stretch (ffmpeg
atempo) instead of Kokoro's native speed= parameter.

Throwaway prototype script — see the plan this implements at
~/.claude/plans/full-audio-book-but-bright-yao.md. Not wired into any router;
run manually:

    cd backend && uv run python scripts/precompile_sample.py [book_id] [--chapter N] [--voice af_heart] [--speeds 0.75,1.0,1.25,1.5,2.0,2.5,3.0]

With no book_id, uses the first book found.

Why atempo instead of native speed= for every tier: Kokoro's own duration
predictor (see services/tts_engine.py's comments) can't honor speed reliably
much past ~1.5-2x — verified in this spike as an audible "taper off/whisper"
artifact at 2.5x/3.0x. atempo is the same class of WSOLA/phase-vocoder
algorithm Speechify/Natural Reader/YouTube's own playback-rate control use:
it stretches the WAVEFORM uniformly rather than asking the model to generate
very short frames, so it degrades as "sounds sped up" instead. It's also
strictly cheaper: one Kokoro synthesis pass per chapter instead of one per
speed tier.

Writes, per requested speed (1.0 is Kokoro-native, everything else atempo):
  scripts/output/sample_<tag>.opus
  scripts/output/manifest_<tag>.json
and scripts/output/tiers.json, an index the test page reads to build the
speed selector (each entry's "method" is "native" or "atempo").
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# This is a flat app (backend/ has no __init__.py package root); running a
# script from backend/scripts/ needs backend/ on sys.path the same way
# `uvicorn main:app` gets it for free by having backend/ as cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sqlmodel import Session, select

import db.database as _db
from db.models import AudioCache, Book, Sentence
from services import engine_manager, export_encoding
from services.tts_engine import SAMPLE_RATE, TTSEngine, normalize_speed

OUTPUT_DIR = Path(__file__).parent / "output"
NATIVE_SPEED = 1.0


def build_engine() -> TTSEngine:
    kokoro, error = engine_manager.build_local("cpu")
    if kokoro is None:
        raise RuntimeError(f"could not build local Kokoro pipeline: {error}")
    return TTSEngine(kokoro)


def load_chapter_sentences(session: Session, book_id: str, chapter: int, limit: int | None) -> list[Sentence]:
    query = (
        select(Sentence)
        .where(Sentence.book_id == book_id, Sentence.chapter == chapter)
        .order_by(Sentence.index)
    )
    if limit:
        query = query.limit(limit)
    sentences = session.exec(query).all()
    if not sentences:
        # Fall back to the book's first few sentences regardless of chapter so
        # the spike still runs against text-only imports (chapter is always 0
        # there, but a mismatched --chapter arg would otherwise yield nothing).
        sentences = session.exec(
            select(Sentence)
            .where(Sentence.book_id == book_id)
            .order_by(Sentence.index)
            .limit(limit or 20)
        ).all()
    return list(sentences)


def synthesize_native_tier(
    engine: TTSEngine,
    sentences: list[Sentence],
    voice: str,
    bitrate: int,
) -> dict:
    """The one Kokoro synthesis pass, always at 1x. Every other tier stretches this."""
    audio_parts: list[np.ndarray] = []
    segments: list[dict] = []
    cumulative_ms = 0.0
    effective_speed = NATIVE_SPEED

    with Session(_db.engine) as session:
        for sentence in sentences:
            audio, effective_speed = engine.synthesize_cached(sentence.text, voice, NATIVE_SPEED)
            if audio is None or audio.size == 0:
                print(f"  [skip] sentence {sentence.index}: no audio produced")
                continue

            cache_key = engine._cache_key(sentence.text, voice, effective_speed)
            row = session.get(AudioCache, cache_key)
            word_timestamps = json.loads(row.word_timestamps) if row and row.word_timestamps else []
            if not word_timestamps:
                # Not fatal (sentence-level sync still works off start/end_ms),
                # but flagged loudly: seen once in this spike from a stale
                # AudioCache row written before timestamps existed on that key.
                print(f"  [warn] sentence {sentence.index}: no word timestamps in cache row "
                      f"(cache_key={cache_key[:12]}...) — word-level highlight will be empty here")

            duration_ms = (audio.shape[0] / SAMPLE_RATE) * 1000.0
            start_ms = cumulative_ms
            end_ms = start_ms + duration_ms

            words = [
                {
                    "text": wt.get("word", ""),
                    "start_ms": start_ms + wt.get("start", 0.0) * 1000.0,
                    "end_ms": start_ms + wt.get("end", 0.0) * 1000.0,
                }
                for wt in word_timestamps
            ]

            try:
                bbox = json.loads(sentence.words) if sentence.words else None
            except (TypeError, ValueError):
                bbox = None

            segments.append({
                "sentence_index": sentence.index,
                "page": sentence.page,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": sentence.text,
                "bbox": {"x0": sentence.x0, "y0": sentence.y0, "x1": sentence.x1, "y1": sentence.y1},
                "words": words,
                "word_bboxes": bbox,
            })

            audio_parts.append(audio)
            cumulative_ms = end_ms

    if not audio_parts:
        raise SystemExit("native 1x tier: no sentences produced audio")

    full_audio = np.concatenate(audio_parts)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = "1"
    audio_name = f"sample_{tag}.opus"
    manifest_name = f"manifest_{tag}.json"
    opus_path = OUTPUT_DIR / audio_name
    export_encoding.encode(full_audio, opus_path, fmt="opus", bitrate_kbps=bitrate)

    manifest = {
        "requested_speed": NATIVE_SPEED,
        "effective_speed": effective_speed,
        "method": "native",
        "sample_rate": SAMPLE_RATE,
        "audio_url": audio_name,
        "duration_ms": cumulative_ms,
        "segments": segments,
    }
    (OUTPUT_DIR / manifest_name).write_text(json.dumps(manifest, indent=2))

    print(f"  tier {tag} (native): effective={effective_speed} "
          f"-> {opus_path.name} ({opus_path.stat().st_size}B), {len(segments)} segments, "
          f"{cumulative_ms:.0f}ms")

    return {
        "tag": tag,
        "requested_speed": NATIVE_SPEED,
        "effective_speed": effective_speed,
        "method": "native",
        "audio_url": audio_name,
        "manifest_url": manifest_name,
        "duration_ms": cumulative_ms,
    }


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


def synthesize_atempo_tier(native_tier: dict, target_speed: float, bitrate: int) -> dict:
    """Derive one speed tier from the native 1x audio via ffmpeg atempo (WSOLA-family stretch)."""
    base_manifest = json.loads((OUTPUT_DIR / native_tier["manifest_url"]).read_text())
    base_audio_path = OUTPUT_DIR / native_tier["audio_url"]
    base_effective_speed = base_manifest["effective_speed"]
    factor = target_speed / base_effective_speed

    tag = f"{target_speed:g}"
    out_path = OUTPUT_DIR / f"sample_{tag}.opus"
    filter_chain = atempo_filter_chain(factor)

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(base_audio_path),
         "-filter:a", filter_chain,
         "-ar", str(base_manifest["sample_rate"]), "-ac", "1",
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

    segments = [
        {
            **seg,
            "start_ms": seg["start_ms"] * scale,
            "end_ms": seg["end_ms"] * scale,
            "words": [
                {**w, "start_ms": w["start_ms"] * scale, "end_ms": w["end_ms"] * scale}
                for w in seg["words"]
            ],
        }
        for seg in base_manifest["segments"]
    ]

    manifest_name = f"manifest_{tag}.json"
    manifest = {
        "requested_speed": target_speed,
        "effective_speed": target_speed,
        "method": "atempo",
        "atempo_detail": f"base={native_tier['tag']}@{base_effective_speed}x, chain={filter_chain}",
        "sample_rate": base_manifest["sample_rate"],
        "audio_url": out_path.name,
        "duration_ms": new_duration_ms,
        "segments": segments,
    }
    (OUTPUT_DIR / manifest_name).write_text(json.dumps(manifest, indent=2))

    print(f"  tier {tag} (atempo, chain={filter_chain}): -> {out_path.name} "
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
    parser.add_argument("book_id", nargs="?", default=None)
    parser.add_argument("--chapter", type=int, default=0)
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--speeds", default="0.75,1.0,1.25,1.5,2.0,2.5,3.0",
                         help="comma-separated speed tiers to produce; only 1.0 is Kokoro-native, "
                              "everything else is derived via atempo from it")
    parser.add_argument("--bitrate", type=int, default=24)
    parser.add_argument("--limit", type=int, default=25,
                         help="max sentences to synthesize (real chapters can run thousands); 0 = no limit")
    args = parser.parse_args()

    _db.create_engine_and_tables()
    speeds = [normalize_speed(float(s)) for s in args.speeds.split(",") if s.strip()]
    if not speeds:
        raise SystemExit("--speeds produced no valid values")
    if NATIVE_SPEED not in speeds:
        speeds.append(NATIVE_SPEED)  # always need the native base to derive everything else from

    with Session(_db.engine) as session:
        if args.book_id:
            book = session.get(Book, args.book_id)
            if book is None:
                raise SystemExit(f"no book with id {args.book_id!r}")
        else:
            book = session.exec(select(Book)).first()
            if book is None:
                raise SystemExit("no books found in the local DB — import one first")

        sentences = load_chapter_sentences(session, book.id, args.chapter, args.limit or None)
        if not sentences:
            raise SystemExit(f"book {book.id} has no sentences to synthesize")

    print(f"book={book.id!r} title={book.title!r} chapter={args.chapter} "
          f"sentences={len(sentences)} voice={args.voice} speeds={speeds}")

    engine = build_engine()

    native_tier = synthesize_native_tier(engine, sentences, args.voice, args.bitrate)
    tiers = [native_tier]
    for speed in speeds:
        if speed == NATIVE_SPEED:
            continue
        tiers.append(synthesize_atempo_tier(native_tier, speed, args.bitrate))

    tiers.sort(key=lambda t: t["requested_speed"])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tiers_path = OUTPUT_DIR / "tiers.json"
    tiers_path.write_text(json.dumps({
        "book_id": book.id,
        "book_title": book.title,
        "chapter": args.chapter,
        "voice": args.voice,
        "tiers": tiers,
    }, indent=2))

    print(f"\nwrote {tiers_path} ({len(tiers)} tiers)")


if __name__ == "__main__":
    main()
