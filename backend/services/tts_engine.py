import asyncio
import hashlib
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import AsyncGenerator, Any

import numpy as np
import soundfile as sf
from sqlmodel import Session

import db.database as _db
from db.models import AudioCache

logger = logging.getLogger(__name__)

INT16_MAX = 32767
SAMPLE_RATE = 24000

# Speeds are normalised to this many decimals before being hashed into a cache
# key. The UI only offers multiples of 0.25 in [0.5, 3.0], so two decimals is
# lossless for every reachable input while collapsing float noise
# (1.15 vs 1.1500000000000001) onto a single key. `_cache_key` must keep emitting
# the legacy `f"{speed}"` spelling for these values, or every row on disk is
# orphaned; see TestCacheKeyBackwardCompatibility.
SPEED_PRECISION = 2

_g2p = None


def _get_g2p():
    """Return the process-wide misaki G2P instance, constructing it on first use.

    ``G2P()`` loads pronunciation dictionaries and a spaCy pipeline, so building
    a fresh one per sentence was pure waste on the word-timestamp path.
    """
    global _g2p
    if _g2p is None:
        from misaki.en import G2P

        _g2p = G2P()
    return _g2p


def _is_spoken_token(token: Any) -> bool:
    """True when a misaki token is a real word rather than bare punctuation.

    This predicate must be applied identically on both the stored-timestamp path
    and the estimated-timestamp path. When the two disagreed, the word list for
    a sentence changed the moment that sentence became cached, and the reader's
    word highlighting visibly shifted underneath the user.
    """
    return bool(token.phonemes) and any(c.isalnum() for c in token.text)


@dataclass
class SynthJob:
    sentence_index: int
    text: str
    voice: str = "af_heart"
    speed: float = 1.0


class TTSEngine:
    def __init__(self, kokoro: Any):
        self.kokoro = kokoro
        self.current_voice = "af_heart"
        self.queue: asyncio.Queue[SynthJob] = asyncio.Queue(maxsize=30)
        self.cancelled: set[int] = set()
        self._sentence_meta: dict[int, dict] = {}
        self._speed_kwarg_supported: bool | None = None  # discovered on first call

    def _proportional_timestamps(self, text: str, duration_ms: int) -> list[dict]:
        """Estimate word timestamps proportionally by phoneme count. ~3ms, no GPU."""
        try:
            _, tokens = _get_g2p()(text)
            words = [(t.text, len(t.phonemes)) for t in tokens if _is_spoken_token(t)]
            total_phonemes = sum(p for _, p in words)
            if total_phonemes == 0:
                return []
            duration_s = duration_ms / 1000.0
            timestamps = []
            offset = 0.0
            for word, phoneme_count in words:
                frac = phoneme_count / total_phonemes
                start = round(offset, 4)
                end = round(offset + frac * duration_s, 4)
                timestamps.append({"word": word, "start": start, "end": end})
                offset = end
            return timestamps
        except Exception:
            return []

    def _cache_key(self, text: str, voice: str, speed: float) -> str:
        """Cache key for one ``(text, voice, speed)`` triple.

        ``repr`` of the rounded float is deliberate. For every speed the UI can
        produce (all multiples of 0.25) it is byte-identical to the legacy
        ``f"{speed}"`` key, so rows already on disk stay reachable. A fixed-point
        format such as ``"1.00"`` would silently orphan the entire existing cache
        and force a re-synthesis of every sentence in the library.
        """
        normalised = repr(round(float(speed), SPEED_PRECISION))
        return hashlib.sha256(f"{text}:{voice}:{normalised}".encode()).hexdigest()

    def _effective_speed(self, requested: float) -> float:
        """The rate audio will actually be produced at.

        Until the first synthesis proves otherwise we optimistically assume the
        requested speed is honoured. Once a call has shown it cannot be, this
        collapses to 1.0 for the lifetime of the engine.
        """
        return 1.0 if self._speed_kwarg_supported is False else requested

    def _call_kokoro(self, text: str, voice: str, speed: float) -> tuple[list, float]:
        """Synthesize `text`, returning ``(results, effective_speed)``.

        ``effective_speed`` is the rate the audio was really produced at. It can
        only differ from ``speed`` on a Kokoro build that rejects the ``speed``
        kwarg, in which case this engine degrades to 1.0x for the rest of the
        session. Callers must key any cache on ``effective_speed`` and never on
        the requested value: keying on the request stored a 1.0x render under the
        1.5x key, permanently, so every later 1.5x request replayed the wrong
        rate straight out of the cache.
        """
        if self._speed_kwarg_supported is False:
            return self.kokoro(text, voice=voice), 1.0

        try:
            results = self.kokoro(text, voice=voice, speed=speed)
        except TypeError:
            logger.warning(
                "Installed Kokoro does not accept the speed= kwarg; "
                "synthesising at 1.0x and labelling the audio as such"
            )
            self._speed_kwarg_supported = False
            return self.kokoro(text, voice=voice), 1.0

        self._speed_kwarg_supported = True
        return results, speed

    def _collect_result(
        self,
        result: Any,
        audio_parts: list[np.ndarray],
        word_timestamps: list[dict],
        audio_offset: float,
    ) -> tuple[np.ndarray, float]:
        audio = result[-1]
        audio_data = audio if isinstance(audio, np.ndarray) else np.array(audio)
        audio_parts.append(audio_data)

        for t in (getattr(result, 'tokens', None) or []):
            if _is_spoken_token(t):
                word_timestamps.append({
                    "word": t.text,
                    "start": round((t.start_ts or 0) + audio_offset, 4),
                    "end": round((t.end_ts or 0) + audio_offset, 4),
                })

        audio_flat = audio_data.flatten() if audio_data.ndim > 1 else audio_data
        return audio_data, audio_offset + len(audio_flat) / SAMPLE_RATE

    @staticmethod
    def _audio_duration_ms(audio_parts: list[np.ndarray]) -> int:
        """Duration of the concatenated audio without materialising the copy."""
        frames = sum(part.shape[0] for part in audio_parts)
        return int(frames / SAMPLE_RATE * 1000)

    def _write_cache_entry(
        self,
        audio_parts: list[np.ndarray],
        word_timestamps: list[dict],
        cache_key: str,
        voice: str,
    ) -> tuple[np.ndarray, int]:
        full_audio = np.concatenate(audio_parts)
        duration_ms = self._audio_duration_ms(audio_parts)
        pcm = (full_audio * INT16_MAX).clip(-INT16_MAX, INT16_MAX).astype(np.int16).tobytes()

        entry = AudioCache(
            text_hash=cache_key,
            audio_data=pcm,
            duration_ms=duration_ms,
            voice=voice,
            word_timestamps=json.dumps(word_timestamps) if word_timestamps else None,
            created_at=datetime.now(UTC),
        )
        with Session(_db.engine) as session:
            if session.get(AudioCache, cache_key) is None:
                session.add(entry)
                session.commit()
        return full_audio, duration_ms

    async def enqueue(self, job: SynthJob) -> None:
        await self.queue.put(job)

    async def stream_job(self, job: SynthJob) -> AsyncGenerator[bytes, None]:
        if job.sentence_index in self.cancelled or self.kokoro is None:
            return

        effective_speed = self._effective_speed(job.speed)
        cache_key = self._cache_key(job.text, job.voice, effective_speed)

        with Session(_db.engine) as session:
            cached = session.get(AudioCache, cache_key)

        if cached is not None:
            word_ts = json.loads(cached.word_timestamps) if cached.word_timestamps else None
            if word_ts is None:
                word_ts = self._proportional_timestamps(job.text, cached.duration_ms)
            self._sentence_meta[job.sentence_index] = {
                "word_timestamps": word_ts,
                "duration_ms": cached.duration_ms,
                "requested_speed": job.speed,
                "effective_speed": effective_speed,
            }
            audio_data = np.frombuffer(cached.audio_data, dtype=np.int16).astype(np.float32) / INT16_MAX
            chunk_samples = SAMPLE_RATE // 10
            for start in range(0, len(audio_data), chunk_samples):
                if job.sentence_index in self.cancelled:
                    return
                chunk = audio_data[start:start + chunk_samples]
                buf = io.BytesIO()
                sf.write(buf, chunk, SAMPLE_RATE, format="WAV", subtype="PCM_16")
                yield buf.getvalue()
            return

        # This call may be the one that discovers the installed Kokoro cannot
        # honour `speed`, so it hands back the rate it actually used.
        results, effective_speed = self._call_kokoro(job.text, job.voice, job.speed)
        audio_parts: list[np.ndarray] = []
        word_timestamps: list[dict] = []
        audio_offset = 0.0
        chunk_samples = SAMPLE_RATE // 10

        for result in results:
            await asyncio.sleep(0)
            if job.sentence_index in self.cancelled:
                return
            audio_chunk, audio_offset = self._collect_result(
                result, audio_parts, word_timestamps, audio_offset,
            )
            for start in range(0, len(audio_chunk), chunk_samples):
                if job.sentence_index in self.cancelled:
                    return
                chunk = audio_chunk[start:start + chunk_samples]
                buf = io.BytesIO()
                sf.write(buf, chunk, SAMPLE_RATE, format="WAV", subtype="PCM_16")
                yield buf.getvalue()

        if audio_parts and job.sentence_index not in self.cancelled:
            # Re-derive the key: `_call_kokoro` above may have just proved the
            # requested speed cannot be honoured, which changes which key this
            # audio legitimately belongs under.
            cache_key = self._cache_key(job.text, job.voice, effective_speed)
            try:
                _, duration_ms = self._write_cache_entry(
                    audio_parts, word_timestamps, cache_key, job.voice,
                )
            except Exception:
                logger.warning(
                    "Failed to cache sentence %s", job.sentence_index, exc_info=True,
                )
                duration_ms = self._audio_duration_ms(audio_parts)
            self._sentence_meta[job.sentence_index] = {
                "word_timestamps": word_timestamps,
                "duration_ms": duration_ms,
                "requested_speed": job.speed,
                "effective_speed": effective_speed,
            }

    async def prefetch(
        self,
        sentences: dict[int, dict],
        from_index: int,
        count: int,
        voice: str,
        speed: float,
        cancel: asyncio.Event,
    ) -> None:
        """Pre-synthesize up to `count` sentences into AudioCache starting at `from_index`."""
        synthesized = 0
        for idx in sorted(sentences.keys()):
            if idx < from_index:
                continue
            if cancel.is_set() or synthesized >= count:
                return
            s = sentences[idx]
            if s["filtered"]:
                continue
            effective_speed = self._effective_speed(speed)
            cache_key = self._cache_key(s["text"], voice, effective_speed)
            with Session(_db.engine) as session:
                already = session.get(AudioCache, cache_key)
            if already is not None:
                synthesized += 1
                await asyncio.sleep(0)
                continue
            try:
                results, effective_speed = self._call_kokoro(s["text"], voice, speed)
                audio_parts: list[np.ndarray] = []
                word_timestamps: list[dict] = []
                audio_offset = 0.0
                for result in results:
                    if cancel.is_set():
                        return
                    _, audio_offset = self._collect_result(
                        result, audio_parts, word_timestamps, audio_offset,
                    )
                    # Kokoro synthesis happens during iteration, so yield between
                    # results or a long sentence monopolises the event loop.
                    await asyncio.sleep(0)
                if audio_parts:
                    self._write_cache_entry(
                        audio_parts,
                        word_timestamps,
                        self._cache_key(s["text"], voice, effective_speed),
                        voice,
                    )
            except Exception:
                logger.warning("Prefetch failed for sentence %s", idx, exc_info=True)
            synthesized += 1
            await asyncio.sleep(0)
