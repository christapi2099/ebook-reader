"""Precompile one chapter's audio: one Kokoro pass at 1x, every other speed
tier derived by audio-domain time-stretch (ffmpeg ``atempo``) rather than
Kokoro's own ``speed=`` parameter.

Why atempo instead of native speed= for every tier: Kokoro's duration
predictor (see ``services/tts_engine.py::normalize_speed``'s docstring) cannot
honor a requested rate reliably much past ~1.5-2x — verified during
prototyping as an audible "taper off / whisper" artifact at 2.5x/3.0x. atempo
is the same class of WSOLA/phase-vocoder algorithm Speechify/Natural
Reader/YouTube's own playback-rate control use: it stretches the *waveform*
uniformly instead of asking the model to generate very short frames, so it
degrades as "sounds sped up" instead. It is also strictly cheaper: one
synthesis pass per chapter instead of one per speed tier.

Restricting Kokoro synthesis to exactly ``speed=1.0`` has a second benefit
beyond quality: this module never reads or writes an ``AudioCache`` row at any
other speed, which is what let a stale non-1.0x cache row (written before
``word_timestamps`` existed on that key, from unrelated earlier use of the
app) get silently reused with no word timestamps during prototyping. That bug
class cannot recur here structurally, not just by convention.

This is the production home of the logic prototyped in
``backend/scripts/precompile_sample.py`` / ``atempo_tiers.py`` (kept in the
repo only as a throwaway reference, not imported from here).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import numpy as np
from sqlmodel import Session, select

import db.database as _db
from db.models import AudioCache, ChapterAudio, Sentence
from services import export_batches, export_encoding
from services.tts_engine import SAMPLE_RATE, TTSEngine, synthesize_serialized

logger = logging.getLogger(__name__)

CHAPTER_AUDIO_DIR = Path("chapter_audio")
NATIVE_SPEED = 1.0
DEFAULT_BITRATE_KBPS = 24
BATCH_SIZE = 100  # progress granularity, not a synthesis constraint


class PrecompileError(RuntimeError):
    pass


def load_chapter_sentences(book_id: str, chapter: int) -> list[Sentence]:
    """Every unfiltered sentence of one chapter, in index order.

    Deliberately queries ``Sentence`` directly rather than
    ``services.sentence_source.load_sentences`` — that helper strips
    page/bbox/word fields (it only serves synthesis-text callers today), and
    the sync manifest needs them for the highlight overlay.
    """
    with Session(_db.engine) as session:
        rows = session.exec(
            select(Sentence)
            .where(Sentence.book_id == book_id, Sentence.chapter == chapter, Sentence.filtered == False)  # noqa: E712
            .order_by(Sentence.index)
        ).all()
        return list(rows)


def _tier_paths(book_id: str, chapter: int, voice: str, tag: str) -> tuple[Path, Path]:
    book_dir = CHAPTER_AUDIO_DIR / book_id
    book_dir.mkdir(parents=True, exist_ok=True)
    audio_path = book_dir / f"{chapter}_{voice}_{tag}.opus"
    manifest_path = book_dir / f"{chapter}_{voice}_{tag}.json"
    return audio_path, manifest_path


def _tag_for(speed: float) -> str:
    return f"{speed:g}"


def run_precompile_blocking(
    engine: TTSEngine,
    row_id_native: int,
    native_needs_work: bool,
    atempo_row_ids_to_build: dict[float, int],
    book_id: str,
    chapter: int,
    voice: str,
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
    update_row=None,
) -> None:
    """Synthesize the native 1x tier if needed, then derive every requested atempo tier.

    ``native_needs_work=False`` means the 1.0x row is already ``done`` — reuse
    its stored audio/manifest paths instead of re-synthesizing, which matters
    both for cost (don't re-run Kokoro on a chapter re-opened after its audio
    already exists) and correctness (re-running would silently clobber a
    `done` row while a caller might be mid-read of it).

    ``update_row(row_id, **fields)`` is a caller-supplied writer (see
    ``routers/precompiled_audio.py::_update_tier``) so this module stays
    free of FastAPI/DB-session concerns beyond reading sentences and
    ``AudioCache``. Deliberately synchronous — Kokoro, ffmpeg and SQLite are
    all blocking calls; the router offloads this whole function to a worker
    thread the same way ``routers/mp3.py::_run_export_blocking`` does.
    """
    def update(row_id: int, **fields) -> None:
        if update_row is not None:
            update_row(row_id, **fields)

    if not native_needs_work:
        with Session(_db.engine) as session:
            native_row = session.get(ChapterAudio, row_id_native)
        if (
            native_row is None or native_row.status != "done"
            or not native_row.audio_path or not native_row.manifest_path
            or not Path(native_row.audio_path).exists()
        ):
            # Row went missing or its file was deleted out from under us
            # between the router's check and this worker thread running —
            # fall back to synthesizing rather than deriving atempo tiers
            # from a file that doesn't exist.
            native_needs_work = True
        else:
            native_audio_path = Path(native_row.audio_path)
            native_manifest_path = Path(native_row.manifest_path)
            effective_speed = native_row.effective_speed or NATIVE_SPEED

    if native_needs_work:
        sentences = load_chapter_sentences(book_id, chapter)
        if not sentences:
            message = "Chapter has no sentences to synthesize"
            update(row_id_native, status="error", phase="error", error_message=message)
            for rid in atempo_row_ids_to_build.values():
                update(rid, status="error", phase="error", error_message=message)
            return

        try:
            native_audio_path, native_manifest_path, effective_speed = _synthesize_native_tier(
                engine, sentences, book_id, chapter, voice, bitrate_kbps, row_id_native, update,
            )
        except Exception as exc:
            message = f"Failed synthesizing native audio: {exc}"
            logger.warning("Precompile %s/%s failed: %s", book_id, chapter, message, exc_info=True)
            update(row_id_native, status="error", phase="error", error_message=message)
            for rid in atempo_row_ids_to_build.values():
                update(rid, status="error", phase="error", error_message="Native tier failed to build")
            return

    for target_speed, row_id in atempo_row_ids_to_build.items():
        try:
            _synthesize_atempo_tier(
                book_id, chapter, voice, native_audio_path, native_manifest_path,
                effective_speed, target_speed, bitrate_kbps, row_id, update,
            )
        except Exception as exc:
            message = f"Failed stretching to {target_speed}x: {exc}"
            logger.warning("Precompile %s/%s @%sx failed: %s", book_id, chapter, target_speed, message, exc_info=True)
            update(row_id, status="error", phase="error", error_message=message)


def _synthesize_native_tier(
    engine: TTSEngine,
    sentences: list[Sentence],
    book_id: str,
    chapter: int,
    voice: str,
    bitrate_kbps: int,
    row_id: int,
    update,
) -> tuple[Path, Path, float]:
    update(row_id, status="processing", phase="processing")

    rows_for_batching = {
        s.index: {"index": s.index, "text": s.text, "chapter": s.chapter, "chapter_title": s.chapter_title}
        for s in sentences
    }
    batches = export_batches.plan_batches(rows_for_batching, max_sentences=BATCH_SIZE)
    by_index = {s.index: s for s in sentences}
    total = sum(len(b.sentence_indices) for b in batches) or 1

    audio_parts: list[np.ndarray] = []
    segments: list[dict] = []
    cumulative_ms = 0.0
    effective_speed = NATIVE_SPEED
    done = 0

    with Session(_db.engine) as session:
        for batch in batches:
            for idx in batch.sentence_indices:
                sentence = by_index[idx]
                audio, effective_speed = synthesize_serialized(engine, sentence.text, voice, NATIVE_SPEED)
                done += 1
                if audio is None or audio.size == 0:
                    continue

                cache_key = engine._cache_key(sentence.text, voice, effective_speed)
                cache_row = session.get(AudioCache, cache_key)
                word_timestamps = (
                    json.loads(cache_row.word_timestamps)
                    if cache_row and cache_row.word_timestamps else []
                )
                if not word_timestamps:
                    logger.warning(
                        "Sentence %s (book=%s chapter=%s): no word timestamps on the "
                        "1.0x AudioCache row — word-level highlight will be empty here",
                        idx, book_id, chapter,
                    )

                duration_ms = (audio.shape[0] / SAMPLE_RATE) * 1000.0
                start_ms = cumulative_ms
                end_ms = start_ms + duration_ms

                try:
                    word_bboxes = json.loads(sentence.words) if sentence.words else None
                except (TypeError, ValueError):
                    word_bboxes = None

                segments.append({
                    "sentence_index": sentence.index,
                    "page": sentence.page,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "bbox": {"x0": sentence.x0, "y0": sentence.y0, "x1": sentence.x1, "y1": sentence.y1},
                    "word_bboxes": word_bboxes,
                    "words": [
                        {
                            "text": wt.get("word", ""),
                            "start_ms": start_ms + wt.get("start", 0.0) * 1000.0,
                            "end_ms": start_ms + wt.get("end", 0.0) * 1000.0,
                        }
                        for wt in word_timestamps
                    ],
                })

                audio_parts.append(audio)
                cumulative_ms = end_ms

            update(row_id, progress=int(done / total * 100))

    if not audio_parts:
        raise PrecompileError("No sentences produced audio")

    full_audio = np.concatenate(audio_parts)
    audio_path, manifest_path = _tier_paths(book_id, chapter, voice, _tag_for(NATIVE_SPEED))
    export_encoding.encode(full_audio, audio_path, fmt="opus", bitrate_kbps=bitrate_kbps)

    manifest = {
        "requested_speed": NATIVE_SPEED,
        "effective_speed": effective_speed,
        "method": "native",
        "sample_rate": SAMPLE_RATE,
        "audio_url": audio_path.name,
        "duration_ms": cumulative_ms,
        "segments": segments,
    }
    manifest_path.write_text(json.dumps(manifest))

    update(
        row_id, status="done", phase="complete", progress=100,
        effective_speed=effective_speed, audio_path=str(audio_path),
        manifest_path=str(manifest_path), duration_ms=cumulative_ms,
        file_size=audio_path.stat().st_size,
    )
    return audio_path, manifest_path, effective_speed


def _atempo_filter_chain(factor: float) -> str:
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


def _ffprobe_duration_ms(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip()) * 1000.0


def _synthesize_atempo_tier(
    book_id: str,
    chapter: int,
    voice: str,
    native_audio_path: Path,
    native_manifest_path: Path,
    native_effective_speed: float,
    target_speed: float,
    bitrate_kbps: int,
    row_id: int,
    update,
) -> None:
    update(row_id, status="processing", phase="processing")

    native_manifest = json.loads(native_manifest_path.read_text())
    factor = target_speed / native_effective_speed
    filter_chain = _atempo_filter_chain(factor)

    audio_path, manifest_path = _tier_paths(book_id, chapter, voice, _tag_for(target_speed))
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(native_audio_path),
         "-filter:a", filter_chain,
         "-ar", str(native_manifest["sample_rate"]), "-ac", "1",
         "-c:a", "libopus", "-b:a", f"{bitrate_kbps}k",
         str(audio_path)],
        capture_output=True, check=True,
    )

    # Measure the actual scale ffmpeg produced rather than trusting `factor`
    # exactly — chained atempo filters and frame rounding mean real output
    # duration can differ from base_duration/factor by a few ms, and that
    # error compounds badly on word-level timestamps left uncorrected.
    base_duration_ms = _ffprobe_duration_ms(native_audio_path)
    new_duration_ms = _ffprobe_duration_ms(audio_path)
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
        for seg in native_manifest["segments"]
    ]

    manifest = {
        "requested_speed": target_speed,
        "effective_speed": target_speed,
        "method": "atempo",
        "atempo_detail": f"base=1.0@{native_effective_speed}x, chain={filter_chain}",
        "sample_rate": native_manifest["sample_rate"],
        "audio_url": audio_path.name,
        "duration_ms": new_duration_ms,
        "segments": segments,
    }
    manifest_path.write_text(json.dumps(manifest))

    update(
        row_id, status="done", phase="complete", progress=100,
        effective_speed=target_speed, audio_path=str(audio_path),
        manifest_path=str(manifest_path), duration_ms=new_duration_ms,
        file_size=audio_path.stat().st_size,
    )
