"""Turn one assembled float32 track into a downloadable file.

Four formats, all fed from the same in-memory audio so every engine, format and
option combination works the same way:

``mp3``
    LAME at an explicit bitrate. The export used to rely on soundfile's default,
    which measured ~61 kbps — an accidental setting nobody chose. 64 kbps mono is
    the LibriVox norm for spoken word, 128 is the option, 192 CBR at 44.1 kHz
    meets ACX/Audible's upload requirement.
``m4b``
    AAC in an MP4 container with chapter markers and tags, which is what
    audiobook players (Apple Books, Cozy, Smart AudioBook Player) need for
    resume and chapter skipping.
``opus``
    The smallest option by a wide margin (24 kbps is Xiph's recommended mono
    audiobook setting) at the cost of partial macOS Safari support on download.
``wav``
    Lossless 16-bit PCM, written by soundfile, no ffmpeg involved.

ffmpeg does the encoding for the first three because soundfile cannot write
AAC/M4B or attach chapters. Everything here is pure except :func:`encode`, which
shells out, so the metadata, chapter maths and argument building are unit
testable without ffmpeg present.
"""

from __future__ import annotations

import base64
import logging
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000


@dataclass(frozen=True)
class FormatSpec:
    """Everything the router needs to validate, describe and encode a format."""

    id: str
    label: str
    extension: str
    content_type: str
    bitrates: tuple[int, ...]
    default_bitrate: int | None
    # Whether chapters/tags can be embedded, and whether ffmpeg is needed at all.
    supports_metadata: bool
    needs_ffmpeg: bool


FORMATS: dict[str, FormatSpec] = {
    "mp3": FormatSpec(
        id="mp3",
        label="MP3",
        extension="mp3",
        content_type="audio/mpeg",
        bitrates=(64, 128, 192),
        default_bitrate=64,
        supports_metadata=True,
        needs_ffmpeg=True,
    ),
    "m4b": FormatSpec(
        id="m4b",
        label="Audiobook (M4B)",
        extension="m4b",
        content_type="audio/mp4",
        bitrates=(64, 96, 128),
        default_bitrate=64,
        supports_metadata=True,
        needs_ffmpeg=True,
    ),
    "opus": FormatSpec(
        id="opus",
        label="Opus (smallest)",
        extension="opus",
        content_type="audio/ogg",
        bitrates=(16, 24, 32),
        default_bitrate=24,
        supports_metadata=True,
        needs_ffmpeg=True,
    ),
    "wav": FormatSpec(
        id="wav",
        label="WAV (lossless)",
        extension="wav",
        content_type="audio/wav",
        bitrates=(),
        default_bitrate=None,
        supports_metadata=False,
        needs_ffmpeg=False,
    ),
}

DEFAULT_FORMAT = "mp3"

FFMPEG_MISSING_MESSAGE = (
    "ffmpeg not found — required for M4B, Opus and chapter export"
)


class EncodingError(RuntimeError):
    """Encoding failed for a reason worth showing the user."""


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def normalise_format(value: str | None) -> str:
    fmt = (value or DEFAULT_FORMAT).strip().lower()
    if fmt not in FORMATS:
        raise ValueError(
            f"Unknown format {value!r}; expected one of {', '.join(sorted(FORMATS))}"
        )
    return fmt


def normalise_bitrate(fmt: str, value: int | None) -> int | None:
    """Validate a requested bitrate against the format's allow-list.

    An explicit allow-list rather than a range: the UI offers three tested
    presets per format, and accepting anything in a range would let a caller
    request a bitrate nobody has listened to.
    """
    spec = FORMATS[fmt]
    if not spec.bitrates:
        if value is not None:
            raise ValueError(f"{spec.label} has no bitrate setting")
        return None
    if value is None:
        return spec.default_bitrate
    try:
        requested = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid bitrate {value!r} for {spec.label}") from None
    if requested not in spec.bitrates:
        allowed = ", ".join(str(b) for b in spec.bitrates)
        raise ValueError(f"{spec.label} bitrate must be one of {allowed} kbps")
    return requested


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------


def escape_metadata_value(value: str) -> str:
    """Escape a value for the ``;FFMETADATA1`` format.

    ffmpeg's parser treats ``\\``, ``;``, ``#`` and ``=`` as special inside a
    value, so each is backslash-escaped. Newlines are collapsed to spaces: the
    format has no portable escape for them, and every value this app writes
    (title, author, chapter name) is a single line by nature — silently joining
    them is better than writing a file ffmpeg refuses to parse.
    """
    collapsed = " ".join(str(value).splitlines()).strip()
    out = []
    for char in collapsed:
        if char in "\\;=#":
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


def build_ffmetadata(
    title: str | None = None,
    author: str | None = None,
    chapters: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Build a ``;FFMETADATA1`` document for ``ffmpeg -i``.

    Chapter blocks carry the audio offsets collected while concatenating, so the
    markers line up with what was actually rendered rather than with an estimate.
    """
    lines = [";FFMETADATA1"]
    if title:
        lines.append(f"title={escape_metadata_value(title)}")
    if author:
        lines.append(f"artist={escape_metadata_value(author)}")
        lines.append(f"album_artist={escape_metadata_value(author)}")

    number = 0
    for chapter in chapters:
        start = int(chapter.get("start_ms", 0))
        end = int(chapter.get("end_ms", start))
        if end <= start:
            continue
        number += 1
        name = chapter.get("title") or f"Chapter {number}"
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start}")
        lines.append(f"END={end}")
        lines.append(f"title={escape_metadata_value(name)}")
    return "\n".join(lines) + "\n"


_UNSET = object()


def chapter_marks(
    sentence_offsets: Mapping[int, tuple[int, int]],
    rows: Mapping[int, Mapping[str, Any]],
    sample_rate: int = SAMPLE_RATE,
) -> list[dict[str, Any]]:
    """Chapter start/end in milliseconds, from real audio offsets.

    ``sentence_offsets`` maps a sentence index to its ``(start, end)`` sample
    position in the assembled track; ``rows`` supplies the chapter each sentence
    belongs to. Sentences absent from ``sentence_offsets`` (filtered, or never
    rendered) cannot contribute a boundary.

    Returns ``[]`` for a book with no meaningful chapters — a single unnamed run
    would put a useless "Chapter 1" marker at 00:00 in every audiobook player.
    """
    marks: list[dict[str, Any]] = []
    current: Any = _UNSET
    start_sample: int | None = None
    end_sample: int | None = None
    title: str | None = None
    fallback_index = 0

    def flush() -> None:
        nonlocal fallback_index
        if start_sample is None or end_sample is None:
            return
        fallback_index += 1
        marks.append(
            {
                "start_ms": int(round(start_sample / sample_rate * 1000)),
                "end_ms": int(round(end_sample / sample_rate * 1000)),
                "title": title,
                "chapter": None if current is _UNSET else current,
            }
        )

    for index in sorted(sentence_offsets):
        row = rows.get(index) or {}
        chapter = row.get("chapter")
        if chapter != current:
            flush()
            current = chapter
            start_sample = sentence_offsets[index][0]
            title = row.get("chapter_title")
        end_sample = sentence_offsets[index][1]
    flush()

    if len(marks) < 2 and not any(mark["title"] for mark in marks):
        return []
    return marks


# --------------------------------------------------------------------------
# Cover art
# --------------------------------------------------------------------------


def flac_picture_block(
    image: bytes,
    *,
    mime: str = "image/jpeg",
    width: int = 0,
    height: int = 0,
    depth: int = 24,
    description: str = "Cover",
) -> bytes:
    """A FLAC ``METADATA_BLOCK_PICTURE`` payload for Ogg Opus.

    ffmpeg 6.1 cannot mux an attached picture into Ogg Opus ("Nothing was written
    into output file"), but the standard Vorbis-comment route works: base64 this
    block into a ``METADATA_BLOCK_PICTURE=`` comment. Written here rather than
    with ``mutagen`` because mutagen is GPL-2.0+ and this app ships as .deb and
    Flatpak.
    """
    picture_type = 3  # front cover
    parts = [
        struct.pack(">I", picture_type),
        struct.pack(">I", len(mime)) + mime.encode("ascii"),
        struct.pack(">I", len(description)) + description.encode("utf-8"),
        struct.pack(">I", int(width)),
        struct.pack(">I", int(height)),
        struct.pack(">I", int(depth)),
        struct.pack(">I", 0),  # palette size, 0 for non-indexed images
        struct.pack(">I", len(image)) + image,
    ]
    return b"".join(parts)


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffmpeg_args(
    fmt: str,
    bitrate_kbps: int | None,
    *,
    has_chapters: bool = False,
    has_metadata: bool = False,
    has_cover: bool = False,
) -> list[str]:
    """Codec and container arguments for one format.

    Kept separate from the input/output plumbing so the per-format decisions are
    testable without running ffmpeg.
    """
    spec = FORMATS[fmt]
    if not spec.needs_ffmpeg:
        raise ValueError(f"{spec.label} is written by soundfile, not ffmpeg")

    if fmt == "mp3":
        args = ["-c:a", "libmp3lame"]
        if bitrate_kbps:
            args += ["-b:a", f"{bitrate_kbps}k"]
        if has_metadata or has_chapters:
            # ID3v2.3 because that is what car stereos and older readers accept.
            args += ["-id3v2_version", "3"]
        if has_cover:
            args += ["-c:v", "copy", "-disposition:v", "attached_pic"]
        return args

    if fmt == "m4b":
        args = ["-c:a", "aac"]
        if bitrate_kbps:
            args += ["-b:a", f"{bitrate_kbps}k"]
        # faststart puts the moov atom first, so a player can seek before the
        # whole file has been read — the difference between "opens instantly"
        # and "downloads 200 MB first" in an audiobook app.
        args += ["-movflags", "+faststart", "-f", "mp4"]
        if has_chapters:
            args += ["-map_chapters", "0"]
        return args

    if fmt == "opus":
        args = ["-c:a", "libopus"]
        if bitrate_kbps:
            args += ["-b:a", f"{bitrate_kbps}k"]
        return args + ["-f", "ogg"]

    raise ValueError(f"No ffmpeg arguments for {fmt!r}")


def encode(
    audio: Any,
    out_path: Path,
    *,
    fmt: str,
    bitrate_kbps: int | None = None,
    metadata: str | None = None,
    cover: bytes | None = None,
    sample_rate: int = SAMPLE_RATE,
) -> Path:
    """Write ``audio`` (float32 mono) to ``out_path`` in ``fmt``.

    WAV goes through soundfile; the rest through ffmpeg, from a scratch WAV next
    to the output. ``metadata`` is an already-built ffmetadata document (see
    :func:`build_ffmetadata`).
    """
    import numpy as np
    import soundfile as sf

    spec = FORMATS[fmt]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)

    if not spec.needs_ffmpeg:
        sf.write(str(out_path), samples, sample_rate, format="WAV", subtype="PCM_16")
        return out_path

    if not ffmpeg_available():
        raise EncodingError(FFMPEG_MISSING_MESSAGE)

    scratch = out_path.with_suffix(".source.wav")
    sf.write(str(scratch), samples, sample_rate, format="WAV", subtype="PCM_16")
    meta_path: Path | None = None
    cover_path: Path | None = None
    try:
        if metadata:
            meta_path = out_path.with_suffix(".ffmetadata")
            meta_path.write_text(metadata, encoding="utf-8")
        if cover and fmt in ("mp3", "m4b"):
            cover_path = out_path.with_suffix(".cover.jpg")
            cover_path.write_bytes(cover)

        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(scratch)]
        if meta_path is not None:
            args += ["-i", str(meta_path)]
        if cover_path is not None:
            args += ["-i", str(cover_path)]

        args += ffmpeg_args(
            fmt,
            bitrate_kbps,
            has_chapters="[CHAPTER]" in (metadata or ""),
            has_metadata=bool(metadata),
            has_cover=cover_path is not None,
        )
        if meta_path is not None:
            args += ["-map_metadata", "1"]
        if cover_path is not None:
            args += ["-map", "0:a", "-map", "2:v"]
        if fmt == "opus" and cover:
            # Ogg Opus gets its cover as a Vorbis comment instead; ffmpeg's
            # attached-picture path writes nothing for this container. The
            # comment carries a base64 FLAC picture block, not the bare JPEG.
            block = base64.b64encode(flac_picture_block(cover)).decode("ascii")
            args += ["-metadata", f"METADATA_BLOCK_PICTURE={block}"]
        args.append(str(out_path))

        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            raise EncodingError(
                f"ffmpeg failed for {spec.label}: "
                f"{(result.stderr or result.stdout or 'no output').strip()[:400]}"
            )
        if not out_path.exists() or out_path.stat().st_size == 0:
            # libsndfile can silently write empty OGG files on some versions;
            # ffmpeg can fail the same way for an unsupported combination.
            raise EncodingError(f"ffmpeg produced an empty {spec.label} file")
    finally:
        for leftover in (scratch, meta_path, cover_path):
            if leftover is not None:
                Path(leftover).unlink(missing_ok=True)
    return out_path
