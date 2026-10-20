"""Process-first render pipeline: fingerprinting (Phase 1b).

The fingerprint identifies *what a render would contain*. Compute device
(cpu/gpu/modal) and playback speed are deliberately NOT inputs.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from services.aligner import ALIGNER_MODEL_ID

_SERVICES_DIR = Path(__file__).resolve().parent

# Modules whose source defines text processing. The sentence splitter lives in
# base_engine.py (BaseEngine._split_sentences).
PIPELINE_SOURCES = ("text_filter.py", "text_cleaner.py", "base_engine.py")

# Speed tiers a new render builds next to the 1.0 base (1.0 is always added).
TIERS: tuple[float, ...] = (1.5, 2.0, 2.5, 3.0)

# The tier list that goes into the fingerprint, pinned to the list every render
# before 2026-10-20 was built with. Changing TIERS must not change the fingerprint
# of published books (that would mark every ready book stale and requeue it, a
# running render included). New renders simply contain fewer tiers; old ones keep
# theirs. Change this only on purpose, to force every book to re-render.
FINGERPRINT_TIERS: tuple[float, ...] = (0.75, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)

# Tier files the renders router serves: today's set plus what older renders contain.
SERVABLE_TIERS: tuple[float, ...] = tuple(sorted({1.0, *TIERS, *FINGERPRINT_TIERS}))

DEFAULT_CODEC = "opus"
DEFAULT_BITRATE = "48k"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_GAP_POLICY = "gap120;nogap-after-,;250-chapter"


def _read_source(name: str) -> str:
    return (_SERVICES_DIR / name).read_text(encoding="utf-8")


def compute_pipeline_version() -> str:
    """sha256 over the source of the text pipeline modules (no manual bump)."""
    h = hashlib.sha256()
    for name in PIPELINE_SOURCES:
        h.update(name.encode())
        h.update(b"\0")
        h.update(_read_source(name).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


PIPELINE_VERSION = compute_pipeline_version()


@dataclass(frozen=True)
class FingerprintInputs:
    voice_name: str
    voice_file_sha256: str
    engine_family: str
    model_revision: str
    aligner_name: str
    aligner_version: str
    aligner_model: str
    ffmpeg_version: str
    pymupdf_version: str
    pipeline_version: str = PIPELINE_VERSION
    codec: str = DEFAULT_CODEC
    bitrate: str = DEFAULT_BITRATE
    sample_rate: int = DEFAULT_SAMPLE_RATE
    tiers: tuple = FINGERPRINT_TIERS
    base_effective_speed: float = 1.0
    gap_policy: str = DEFAULT_GAP_POLICY


def fingerprint(inputs: FingerprintInputs) -> str:
    """Pure: sha256 hex over the canonical JSON of the inputs."""
    d = asdict(inputs)
    d["tiers"] = [float(t) for t in inputs.tiers]
    blob = json.dumps(d, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _pymupdf_version() -> str:
    import pymupdf
    return pymupdf.VersionBind


def _aligner_version(name: str) -> str:
    from importlib import metadata
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _ffmpeg_version() -> str:
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                         text=True, timeout=10).stdout
    return out.splitlines()[0] if out else "unknown"


def _voice_hash(voice: str, voice_path: Optional[str]) -> str:
    """Hash of the voice file; built-in Kokoro voices have no file here, so
    fall back to the sha256 of the voice name string."""
    if voice_path and Path(voice_path).is_file():
        h = hashlib.sha256()
        with open(voice_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    return hashlib.sha256(voice.encode("utf-8")).hexdigest()


def current_inputs(
    voice: str,
    engine_family: str,
    *,
    voice_path: Optional[str] = None,
    voices_dir: Optional[str] = None,
    model_revision: str = "unknown",
    aligner_name: str = "ctc-forced-aligner",
    aligner_version: Optional[str] = None,
    aligner_model: str = ALIGNER_MODEL_ID,
    ffmpeg_version: Optional[str] = None,
    **overrides,
) -> FingerprintInputs:
    """Gather live inputs. ``voices_dir`` + ``{voice}.onnx`` is probed when no
    explicit ``voice_path`` is given."""
    if voice_path is None and voices_dir:
        voice_path = str(Path(voices_dir) / f"{voice}.onnx")
    if aligner_version is None:
        aligner_version = _aligner_version(aligner_name)
    return FingerprintInputs(
        voice_name=voice,
        voice_file_sha256=_voice_hash(voice, voice_path),
        engine_family=engine_family,
        model_revision=model_revision,
        aligner_name=aligner_name,
        aligner_version=aligner_version,
        aligner_model=aligner_model,
        ffmpeg_version=ffmpeg_version if ffmpeg_version is not None else _ffmpeg_version(),
        pymupdf_version=_pymupdf_version(),
        **overrides,
    )
