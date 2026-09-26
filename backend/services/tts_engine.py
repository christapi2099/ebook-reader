import asyncio
import hashlib
import inspect
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
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
# key. Two decimals is lossless for every speed the UI offers (multiples of 0.25
# in [0.5, 3.0]) while collapsing float noise (1.15 vs 1.1500000000000001) onto a
# single key. `_cache_key` must keep emitting the legacy `f"{speed}"` spelling for
# those values, or every row already on disk is orphaned; see
# TestCacheKeyBackwardCompatibility.
#
# Caveat: two *client-supplied* speeds inside the same 0.01 bucket do collide
# (1.149 and 1.15 share a key), which bounds the worst-case rate error at 1.92%
# at the 0.5x end. Unreachable from the UI. The durable fix is to quantise once
# at the transport boundary so the key, the argument handed to Kokoro and the
# rate reported back can never disagree.
SPEED_PRECISION = 2

# How much audio prefetch tries to keep warm ahead of the playhead, in seconds.
# The bound used to be a sentence COUNT (50), which says nothing about how much
# audio the user actually has buffered: at 1.0x on this corpus 50 sentences is
# roughly 185 s of audio and takes about 478 s to generate on CPU (RTF 1.77), so
# a burst could never finish before being cancelled and merely monopolised the
# synthesis worker. A time budget is the unit that matters, and it adapts to
# speed for free, since a higher speed renders a shorter sentence.
PREFETCH_TARGET_AUDIO_SECONDS = 60.0

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


# Kokoro inference is synchronous and a single sentence can take tens of seconds,
# so it is run on a dedicated worker thread. Without this the whole event loop
# freezes for the length of every sentence: WebSocket frames stall, /health stops
# answering, and asyncio cancellation is not observed until the sentence ends.
# (Measured on this codebase: 83 consecutive seconds with zero event-loop turns.)
#
# max_workers=1 is deliberate and load-bearing:
#   * it SERIALISES synthesis, so a prefetch can never render concurrently with
#     the sentence the user is actually waiting for;
#   * it stops torch oversubscribing - two concurrent inferences would contend
#     for the same cores and the same CUDA context.
# The pool is module-level because routers/tts.py builds a TTSEngine per
# WebSocket connection, so a per-instance pool would permit exactly the
# concurrency max_workers=1 exists to prevent.
_synthesis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kokoro-synth")

# Number of sentences currently waiting on the synthesis worker for playback.
# Module-level for the same reason as the pool. Prefetch reads it and stands
# down while it is non-zero, so a batch can never starve live audio.
_playback_waiting = 0

_SYNTHESIS_EXHAUSTED = object()


def _next_chunk(iterator: Any) -> Any:
    """Pull the next item from a Kokoro result iterator, or the sentinel.

    Runs on the synthesis worker thread. For a generator-based Kokoro, *nexting*
    the iterator is what actually performs inference for that chunk, so this is
    the call that has to be off the event loop.
    """
    try:
        return next(iterator)
    except StopIteration:
        return _SYNTHESIS_EXHAUSTED


def _playback_wait_begin() -> None:
    """Mark that a sentence the user is waiting for needs the synth worker."""
    global _playback_waiting
    _playback_waiting += 1


def _playback_wait_end() -> None:
    global _playback_waiting
    _playback_waiting -= 1


def _accepts_speed(kokoro: Any) -> bool:
    """Whether `kokoro` accepts a ``speed=`` keyword.

    Probing the signature is strictly better than calling and catching TypeError.
    A TypeError can originate anywhere inside a callable that is not a generator
    function — an unrelated bug, a particular input, a remote transport, or a
    plain ``None`` — and treating any of those as "this build has no speed
    support" silently and *permanently* downgrades a healthy engine to 1.0x for
    the rest of the session. Only a genuine signature mismatch should do that.
    """
    try:
        return "speed" in inspect.signature(kokoro).parameters
    except (TypeError, ValueError):
        # Not introspectable (builtins, some C callables). Give it the benefit of
        # the doubt and let any real error surface from the call itself.
        return True


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
        only differ from ``speed`` when the injected Kokoro callable takes no
        ``speed`` keyword, in which case this engine degrades to 1.0x for the rest
        of the session. Callers must key any cache on ``effective_speed`` and
        never on the requested value: keying on the request stored a 1.0x render
        under the 1.5x key, permanently, so every later 1.5x request replayed the
        wrong rate straight out of the cache.
        """
        if self._speed_kwarg_supported is None:
            self._speed_kwarg_supported = _accepts_speed(self.kokoro)
            if not self._speed_kwarg_supported:
                logger.warning(
                    "Injected Kokoro callable takes no speed= keyword; "
                    "synthesising at 1.0x and labelling the audio as such"
                )

        if not self._speed_kwarg_supported:
            return self.kokoro(text, voice=voice), 1.0
        return self.kokoro(text, voice=voice, speed=speed), speed

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

        # `None` means "this token carries no timing", not "this token starts at
        # zero". Substituting 0.0 made the word look as though it began at the
        # start of the sentence, and the frontend picks the last word whose
        # `start` has already passed — so it highlighted that word early and then
        # stalled on it until a later word with a real start arrived. Carrying the
        # previous word's end forward keeps the sequence monotonic and the
        # highlight moving.
        #
        # The entry is appended even when timings are missing: the frontend
        # addresses words by POSITION (`currentWordIndex` indexes into this list),
        # so dropping one would desynchronise every word after it.
        previous_end = 0.0
        for t in (getattr(result, 'tokens', None) or []):
            if not _is_spoken_token(t):
                continue
            start = t.start_ts if t.start_ts is not None else previous_end
            end = t.end_ts if t.end_ts is not None else start
            if end < start:
                end = start
            word_timestamps.append({
                "word": t.text,
                "start": round(start + audio_offset, 4),
                "end": round(end + audio_offset, 4),
            })
            previous_end = end

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
        # honour `speed`, so it hands back the rate it actually used. It goes to
        # the worker thread as well: for a generator-based Kokoro the call itself
        # is cheap, but not every build is a generator.
        loop = asyncio.get_running_loop()
        _playback_wait_begin()
        try:
            results, effective_speed = await loop.run_in_executor(
                _synthesis_pool, self._call_kokoro, job.text, job.voice, job.speed,
            )
            audio_parts: list[np.ndarray] = []
            word_timestamps: list[dict] = []
            audio_offset = 0.0
            chunk_samples = SAMPLE_RATE // 10

            iterator = iter(results)
            while True:
                result = await loop.run_in_executor(
                    _synthesis_pool, _next_chunk, iterator,
                )
                if result is _SYNTHESIS_EXHAUSTED:
                    break
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
        finally:
            _playback_wait_end()

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
        target_audio_seconds: float = PREFETCH_TARGET_AUDIO_SECONDS,
    ) -> None:
        """Warm the cache ahead of `from_index` for up to `count` sentences.

        Two bounds apply. `target_audio_seconds` is the one that matters: it
        bounds the batch by how much *listening time* is buffered rather than by
        how many sentences were touched, so a burst stops as soon as the user has
        enough audio ahead of them and the worker is released. `count` remains as
        a hard safety cap. Audio that is already cached counts towards the budget,
        so a warm run returns almost immediately.

        Prefetch shares the one synthesis worker with live playback, so it stands
        down whenever a sentence the user is waiting for needs that worker.
        Without the gate a batch would hold the worker for all `count` sentences
        and live audio would queue behind it — and on CPU the prefetcher cannot
        keep up anyway (it is slower than real time), so its work during playback
        buys nothing while costing latency. It therefore warms ahead when the user
        is not waiting, and yields when they are.
        """
        if self.kokoro is None or target_audio_seconds <= 0:
            return

        loop = asyncio.get_running_loop()
        synthesized = 0
        buffered_seconds = 0.0
        for idx in sorted(sentences.keys()):
            if idx < from_index:
                continue
            if cancel.is_set() or synthesized >= count:
                return
            if buffered_seconds >= target_audio_seconds:
                return
            s = sentences[idx]
            if s["filtered"]:
                continue

            while _playback_waiting and not cancel.is_set():
                await asyncio.sleep(0.05)
            if cancel.is_set():
                return

            effective_speed = self._effective_speed(speed)
            cache_key = self._cache_key(s["text"], voice, effective_speed)
            with Session(_db.engine) as session:
                already = session.get(AudioCache, cache_key)
            if already is not None:
                buffered_seconds += already.duration_ms / 1000.0
                synthesized += 1
                await asyncio.sleep(0)
                continue
            try:
                results, effective_speed = await loop.run_in_executor(
                    _synthesis_pool, self._call_kokoro, s["text"], voice, speed,
                )
                audio_parts: list[np.ndarray] = []
                word_timestamps: list[dict] = []
                audio_offset = 0.0
                iterator = iter(results)
                while True:
                    result = await loop.run_in_executor(
                        _synthesis_pool, _next_chunk, iterator,
                    )
                    if result is _SYNTHESIS_EXHAUSTED:
                        break
                    if cancel.is_set():
                        return
                    _, audio_offset = self._collect_result(
                        result, audio_parts, word_timestamps, audio_offset,
                    )
                if audio_parts:
                    _, duration_ms = self._write_cache_entry(
                        audio_parts,
                        word_timestamps,
                        self._cache_key(s["text"], voice, effective_speed),
                        voice,
                    )
                    buffered_seconds += duration_ms / 1000.0
            except Exception:
                logger.warning("Prefetch failed for sentence %s", idx, exc_info=True)
            synthesized += 1
            await asyncio.sleep(0)
