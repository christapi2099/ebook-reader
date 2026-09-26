"""Storage codec for the audio cache.

The audio cache is the largest thing this app stores. Eviction caps it at
``AUDIO_CACHE_MAX_MB`` (4 GB by default) of *stored* bytes, and at 24 kHz mono
PCM_16 that is roughly three books. Ogg Opus at 24 kbps holds an order of
magnitude more audio under the same cap, which is the only reason this module
exists.

Measured on this machine, 1 s of 24 kHz mono: 48 000 PCM_16 bytes became 3 291
Opus bytes, a factor of 14.6. The plan's corpus measurement was 16.4x (2.18 MB
WAV against 133 KB at 24 kbps), so the two agree.

``pcm16`` is the default and writes byte-for-byte what the cache stored before
this module existed. ``opus`` is opt-in through ``AUDIO_CACHE_CODEC`` because it
trades encoder CPU on the synthesis path for disk, and that trade has not been
measured against the user's own corpus yet.

The **wire format is deliberately unaffected**. `TTSEngine.stream_job` still
chunks WAV to the WebSocket whatever the cache holds, because the browser decodes
each binary frame with `decodeAudioData` — which does not support Ogg Opus
everywhere — and because the transport is loopback HTTP, where a compressed frame
buys nothing while a decoder that refuses one costs the whole book. Only the
bytes at rest change.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Mapping

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

PCM16 = "pcm16"
OPUS = "opus"
SUPPORTED_CODECS = (PCM16, OPUS)
DEFAULT_CODEC = PCM16

CACHE_CODEC_ENV = "AUDIO_CACHE_CODEC"

# Xiph's recommended rate for mono audiobook speech, and the same default the
# Opus export uses (docs/plans/modal-engine-ui-and-export-formats.md §5).
OPUS_BITRATE_KBPS = 24

INT16_MAX = 32767


def resolve_cache_codec(env: Mapping[str, str] | None = None) -> str:
    """The configured cache codec, or ``pcm16`` for anything unrecognised.

    Follows :mod:`services.env_config`: an absent variable is silent, while a
    present-but-useless one is logged and ignored. Being tolerant here matters
    more than usual because the value selects a *storage format* — refusing to
    start, or half-reading rows written under the other codec, would be far worse
    than quietly storing PCM.
    """
    environment = os.environ if env is None else env
    raw = (environment.get(CACHE_CODEC_ENV) or "").strip().lower()
    if not raw:
        return DEFAULT_CODEC
    if raw not in SUPPORTED_CODECS:
        logger.warning(
            "Unknown %s=%r; storing the audio cache as %s. Supported: %s",
            CACHE_CODEC_ENV, raw, DEFAULT_CODEC, ", ".join(SUPPORTED_CODECS),
        )
        return DEFAULT_CODEC
    return raw


def encode(audio: np.ndarray, codec: str | None = None) -> tuple[bytes, str]:
    """Serialise ``audio`` (float32 mono) for storage, with the codec used.

    Returns the codec that was *actually* used, which is not always the one asked
    for: Opus needs ffmpeg, and a missing binary must not fail a synthesis the
    reader is waiting on. Falling back to PCM_16 keeps the audio, which is the
    only outcome that matters on this path.
    """
    chosen = codec or resolve_cache_codec()
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if chosen == OPUS:
        blob = _encode_opus(samples)
        if blob is not None:
            return blob, OPUS
    return _encode_pcm16(samples), PCM16


def decode(blob: bytes, codec: str | None = None) -> np.ndarray:
    """Read a stored blob back as float32 mono, whichever codec wrote it.

    A row with no codec predates the column and is therefore PCM_16 — the same
    thing the migration's default says. Raises on a blob the codec cannot read,
    so a caller on the synthesis path can treat that as a cache miss and render
    the sentence again rather than serving noise.
    """
    if (codec or DEFAULT_CODEC) == OPUS:
        data, _rate = sf.read(io.BytesIO(blob), dtype="float32")
        return np.asarray(data, dtype=np.float32).reshape(-1)
    return np.frombuffer(blob, dtype=np.int16).astype(np.float32) / INT16_MAX


def _encode_pcm16(samples: np.ndarray) -> bytes:
    """PCM_16 bytes, exactly as the cache produced them before this module."""
    return (samples * INT16_MAX).clip(-INT16_MAX, INT16_MAX).astype(np.int16).tobytes()


def _encode_opus(samples: np.ndarray) -> bytes | None:
    """Ogg Opus bytes for ``samples``, or ``None`` if this machine cannot encode.

    The import is deferred because :mod:`services.export_encoding` is the module
    that owns the ffmpeg argument list, including the constrained-VBR flag that
    makes ``-b:a`` mean anything, and importing it at module scope would tie this
    small storage helper to the whole export stack.

    Opus's encoder delay is **not** trimmed by hand. RFC 7845 says the decoder
    must discard the pre-skip, and the decoder used on the way back
    (``soundfile`` over libsndfile) does: a 24 000-sample input decoded to exactly
    24 000 samples, so the stored word timestamps stay aligned with the audio with
    no correction. That was measured rather than assumed, because the plan flagged
    it as a risk to check before enabling this codec.
    """
    from services.export_encoding import EncodingError
    from services.export_encoding import encode as encode_file

    with tempfile.TemporaryDirectory(prefix="audiocache-opus-") as scratch:
        out_path = Path(scratch) / "sentence.opus"
        try:
            encode_file(samples, out_path, fmt=OPUS, bitrate_kbps=OPUS_BITRATE_KBPS)
        except EncodingError:
            logger.warning(
                "Opus cache encoding is unavailable; storing this sentence as PCM_16",
                exc_info=True,
            )
            return None
        except OSError:
            # Disk full or an unwritable temp dir. The sentence is already
            # synthesized, so keep it rather than losing the work.
            logger.warning(
                "Could not encode an Opus cache entry; storing PCM_16", exc_info=True,
            )
            return None
        return out_path.read_bytes()
