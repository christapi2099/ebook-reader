import asyncio
import hashlib
import inspect
import io
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import AsyncGenerator, Any

import numpy as np
import soundfile as sf
from sqlmodel import Session

import db.database as _db
from db.models import AudioCache
from services import audio_cache_codec

logger = logging.getLogger(__name__)

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

# The band the API accepts. Out-of-band values are clamped rather than rejected, so
# tightening the range cannot break an existing client, and 3.0 stays reachable
# for the cache rows already keyed there.
MIN_SPEED = 0.5
MAX_SPEED = 3.0


def normalize_speed(raw: Any) -> float:
    """Validate and quantise a requested playback rate, or raise ValueError.

    Every synthesis entry point funnels through here, so the cache key, the
    argument handed to Kokoro and the rate reported back to the client cannot
    disagree about which rate was asked for.

    Two failure modes motivate the checks. Kokoro's duration predictor divides the
    predicted frame durations by ``speed`` and then floors every phoneme at one
    25 ms frame, so the delivered rate saturates well below the request — a 3.0x
    request renders at roughly 2.2x. A non-finite speed is worse than merely
    inaccurate: ``clamp(min=1)`` is applied to the float *before* the cast to
    long, so ``inf`` and ``NaN`` survive it and become INT64_MIN, which makes
    ``repeat_interleave`` raise during iteration — outside ``_call_kokoro``, so
    nothing on the synthesis path catches it and the session dies.
    """
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        raise ValueError("speed must be a number") from None
    if not math.isfinite(speed):
        raise ValueError("speed must be finite")
    if speed <= 0:
        raise ValueError("speed must be greater than zero")
    return round(min(max(speed, MIN_SPEED), MAX_SPEED), SPEED_PRECISION)

# How much audio prefetch tries to keep warm ahead of the playhead, in seconds.
# The bound used to be a sentence COUNT (50), which says nothing about how much
# audio the user actually has buffered: at 1.0x on this corpus 50 sentences is
# roughly 185 s of audio and takes about 478 s to generate on CPU (RTF 1.77), so
# a burst could never finish before being cancelled and merely monopolised the
# synthesis worker. A time budget is the unit that matters, and it adapts to
# speed for free, since a higher speed renders a shorter sentence.
PREFETCH_TARGET_AUDIO_SECONDS = 60.0

# Sentences covered by one ``synthesize_many`` round trip on the remote backend.
# The deployed Modal app takes a list of texts, so "batching" means one request
# for several sentences instead of one request each: measured, 3 sentences in a
# single batch took 1.09 s against 0.50 s for one call, so the same audio costs
# the shared synthesis worker *less* time, not more. The cap is what keeps a
# batch from holding that one worker for a whole chapter at a stretch; the
# audio-time budget below usually stops it sooner, and the stand-down gate stops
# it entirely while the user is waiting for a sentence of their own.
PREFETCH_BATCH_MAX = 8

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


def _cached_audio(row: AudioCache) -> np.ndarray | None:
    """One cache row's audio as float32 mono, or ``None`` if it cannot be read.

    The codec is read off the row rather than assumed, so rows written under the
    other codec — including every row that predates the column — stay readable.
    A row that cannot be decoded returns ``None`` instead of raising, and every
    caller treats that as a cache miss and synthesises the sentence again: a
    corrupt or half-written entry should cost one re-synthesis, not the request
    that happened to read it.
    """
    try:
        return audio_cache_codec.decode(row.audio_data, row.codec)
    except Exception:
        logger.warning(
            "Could not decode cached audio %s (codec=%r); re-synthesising",
            row.text_hash, getattr(row, "codec", None), exc_info=True,
        )
        return None


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


def synthesize_serialized(
    engine: "TTSEngine", text: str, voice: str, speed: float
) -> tuple[np.ndarray | None, float]:
    """Run ``engine.synthesize_cached`` on the shared synthesis worker.

    Blocking, so callers must already be off the event loop. Exported so that
    every synthesis path — streaming playback, prefetch and the MP3 export — goes
    through the same single worker, which is what stops two of them rendering
    concurrently against the same CPU cores and CUDA context.
    """
    return _synthesis_pool.submit(engine.synthesize_cached, text, voice, speed).result()


def _batch_client(engine: "TTSEngine") -> Any | None:
    """The engine to batch prefetch against, or ``None`` for the serial path.

    Two clauses, and both matter. The manager is the authority on *which* engine
    is live — the resolved remote transport is its answer, read from one place
    instead of guessed per sentence — and the object the batch is issued to is
    the one the rest of the session synthesises with (``engine.kokoro``), so a
    batch can never mix engines with the audio being streamed.

    A local backend returns ``None`` here and takes the unchanged serial path: a
    local ``synthesize_many`` would render the same sentences serially anyway, so
    a batch would only add a code path — and this must not change local
    behaviour by a single byte.
    """
    from services import engine_manager

    if engine_manager.manager.active() != engine_manager.MODAL:
        return None
    kokoro = engine.kokoro
    if not callable(getattr(kokoro, "synthesize_many", None)):
        return None
    return kokoro


def _seconds_per_sentence(measured: list[float], fallback: float) -> float:
    """Mean duration of the sentences measured so far, or ``fallback``.

    Needed to decide how many not-yet-synthesised sentences fit in one batch
    under the audio-time budget before any of them has been rendered. With
    nothing measured yet the fallback is the whole budget, which makes the first
    window a single sentence — exactly the serial path's own first step — and the
    estimate only improves from there.
    """
    return sum(measured) / len(measured) if measured else fallback


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

    def _resolve_speed_support(self) -> bool:
        """Whether the injected engine accepts ``speed=``, probed once.

        The answer is a property of the callable, not of a sentence, so it is
        discovered on the first synthesis and reused for the life of the engine.
        """
        if self._speed_kwarg_supported is None:
            self._speed_kwarg_supported = _accepts_speed(self.kokoro)
            if not self._speed_kwarg_supported:
                logger.warning(
                    "Injected Kokoro callable takes no speed= keyword; "
                    "synthesising at 1.0x and labelling the audio as such"
                )
        return self._speed_kwarg_supported

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
        if not self._resolve_speed_support():
            return self.kokoro(text, voice=voice), 1.0
        return self.kokoro(text, voice=voice, speed=speed), speed

    def _call_kokoro_many(
        self, texts: list[str], voice: str, speed: float
    ) -> tuple[list[list], float]:
        """Synthesize several sentences in one remote round trip.

        The batch twin of ``_call_kokoro``, with the same speed handling, so a
        cache row written from either path is keyed on the rate the audio was
        really produced at. Blocking, so callers keep it on the shared synthesis
        worker.
        """
        if not self._resolve_speed_support():
            return self.kokoro.synthesize_many(texts, voice=voice), 1.0
        return self.kokoro.synthesize_many(texts, voice=voice, speed=speed), speed

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
        # The one place a cache row is written. The codec is resolved here and
        # recorded on the row, so reads do not have to guess and the setting can
        # be changed — or reverted — without rewriting stored audio.
        blob, codec = audio_cache_codec.encode(full_audio)

        entry = AudioCache(
            text_hash=cache_key,
            audio_data=blob,
            codec=codec,
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

    def _store_group(
        self, group: list, text: str, voice: str, effective_speed: float
    ) -> float | None:
        """Cache one sentence's chunk group under its own key.

        Returns the audio duration in seconds, or ``None`` when the group carried
        no audio. Batching is a transport detail and nothing more: every sentence
        still lands under ``_cache_key(text, voice, effective_speed)``, exactly
        where the serial path would have put it, so a later playback is a cache
        hit and not a second synthesis.
        """
        audio_parts: list[np.ndarray] = []
        word_timestamps: list[dict] = []
        audio_offset = 0.0
        for result in group:
            _, audio_offset = self._collect_result(
                result, audio_parts, word_timestamps, audio_offset,
            )
        if not audio_parts:
            return None

        _, duration_ms = self._write_cache_entry(
            audio_parts,
            word_timestamps,
            self._cache_key(text, voice, effective_speed),
            voice,
        )
        return duration_ms / 1000.0

    def synthesize_cached(
        self, text: str, voice: str, speed: float
    ) -> tuple[np.ndarray | None, float]:
        """Synthesize one sentence, reusing AudioCache.

        Returns ``(audio, effective_speed)``, with ``audio`` None when nothing was
        produced. Blocking and synchronous: callers must keep it off the event
        loop, and ``synthesize_serialized`` is the usual way in.

        This exists so the MP3 export path shares this engine's cache *and* its
        speed-capability handling instead of duplicating both. Duplicating them is
        how the export came to re-render audio the reader already had, and to
        record a playback rate the build may never have rendered.
        """
        effective_speed = self._effective_speed(speed)
        cache_key = self._cache_key(text, voice, effective_speed)

        with Session(_db.engine) as session:
            cached = session.get(AudioCache, cache_key)
        if cached is not None:
            audio = _cached_audio(cached)
            if audio is not None:
                return audio, effective_speed
            # Undecodable entry — treat it as a miss and render it again.

        if self.kokoro is None:
            return None, effective_speed

        results, effective_speed = self._call_kokoro(text, voice, speed)
        audio_parts: list[np.ndarray] = []
        word_timestamps: list[dict] = []
        audio_offset = 0.0
        for result in results:
            _, audio_offset = self._collect_result(
                result, audio_parts, word_timestamps, audio_offset,
            )
        if not audio_parts:
            return None, effective_speed

        try:
            self._write_cache_entry(
                audio_parts,
                word_timestamps,
                self._cache_key(text, voice, effective_speed),
                voice,
            )
        except Exception:
            logger.warning("Failed to cache a synthesised sentence", exc_info=True)
        return np.concatenate(audio_parts), effective_speed

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
            audio_data = _cached_audio(cached)
            if audio_data is not None:
                word_ts = json.loads(cached.word_timestamps) if cached.word_timestamps else None
                if word_ts is None:
                    word_ts = self._proportional_timestamps(job.text, cached.duration_ms)
                self._sentence_meta[job.sentence_index] = {
                    "word_timestamps": word_ts,
                    "duration_ms": cached.duration_ms,
                    "requested_speed": job.speed,
                    "effective_speed": effective_speed,
                }
                # The wire format is WAV whatever the row holds: the client
                # decodes every frame with `decodeAudioData`, so the storage codec
                # must not leak into the transport. Decoded back to float32 here,
                # then rechunked exactly as before.
                chunk_samples = SAMPLE_RATE // 10
                for start in range(0, len(audio_data), chunk_samples):
                    if job.sentence_index in self.cancelled:
                        return
                    chunk = audio_data[start:start + chunk_samples]
                    buf = io.BytesIO()
                    sf.write(buf, chunk, SAMPLE_RATE, format="WAV", subtype="PCM_16")
                    yield buf.getvalue()
                return
            # Undecodable entry: fall through and synthesise it again rather than
            # streaming noise or failing the session. `_cached_audio` logged why.

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

        On the remote backend those same bounds are applied to a whole window at
        once, which is sent as a single ``synthesize_many`` round trip instead of
        one call per sentence (``_prefetch_remote``). A local backend takes the
        serial path below, unchanged: it has nothing to gain from a batch.
        """
        if self.kokoro is None or target_audio_seconds <= 0:
            return

        if _batch_client(self) is not None:
            await self._prefetch_remote(
                sentences, from_index, count, voice, speed, cancel, target_audio_seconds,
            )
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

    async def _prefetch_remote(
        self,
        sentences: dict[int, dict],
        from_index: int,
        count: int,
        voice: str,
        speed: float,
        cancel: asyncio.Event,
        target_audio_seconds: float,
    ) -> None:
        """Prefetch the remote backend one ``synthesize_many`` batch at a time.

        Reached only when ``_batch_client`` says the live transport is remote, so
        the local serial path above is untouched. The bounds are the same ones and
        they are applied to the window as a whole: `count` is the hard cap,
        `target_audio_seconds` is the audio-time budget, and the one shared
        synthesis worker is released between windows. A window holds that worker
        for a single round trip covering at most ``PREFETCH_BATCH_MAX`` sentences,
        which is measurably shorter than the same sentences one call at a time
        (3 in 1.09 s against 0.50 s for one), so batching shortens the window it
        holds the worker rather than lengthening it.
        """
        loop = asyncio.get_running_loop()
        order = [idx for idx in sorted(sentences) if idx >= from_index]
        measured_seconds: list[float] = []
        synthesized = 0
        buffered_seconds = 0.0
        position = 0

        while position < len(order):
            if cancel.is_set() or synthesized >= count:
                return
            if buffered_seconds >= target_audio_seconds:
                return

            # The same stand-down as the serial path: the one synthesis worker is
            # shared with live playback, and a batch queued ahead of a sentence
            # the user is waiting for is heard as a stall.
            while _playback_waiting and not cancel.is_set():
                await asyncio.sleep(0.05)
            if cancel.is_set():
                return

            # Resolved before anything is keyed: the cache key has to name the
            # rate the engine will really render at, and resolving it here means
            # the value used for the cache lookup, the batch request and the write
            # cannot disagree.
            self._resolve_speed_support()
            effective_speed = self._effective_speed(speed)

            window: list[tuple[int, str]] = []
            while position < len(order) and len(window) < PREFETCH_BATCH_MAX:
                idx = order[position]
                sentence = sentences[idx]
                if sentence["filtered"]:
                    position += 1
                    continue
                if synthesized >= count or buffered_seconds >= target_audio_seconds:
                    # Stop here but do NOT consume the sentence: the bounds are
                    # re-checked at the top of the next window, and a candidate
                    # dropped on the way out would never be prefetched at all.
                    break

                cache_key = self._cache_key(sentence["text"], voice, effective_speed)
                with Session(_db.engine) as session:
                    already = session.get(AudioCache, cache_key)
                if already is not None:
                    position += 1
                    seconds = already.duration_ms / 1000.0
                    buffered_seconds += seconds
                    measured_seconds.append(seconds)
                    synthesized += 1
                    continue

                if synthesized + len(window) >= count:
                    break
                # The budget has to be checked before the request, not after: once
                # the batch has been sent, the GPU time is already paid for. The
                # sentences already measured in this run are the estimate of what
                # one more costs; with nothing measured yet the whole budget is
                # the estimate, so the first window is a single sentence.
                projected = buffered_seconds + _seconds_per_sentence(
                    measured_seconds, target_audio_seconds
                ) * (len(window) + 1)
                if window and projected > target_audio_seconds:
                    break
                window.append((idx, sentence["text"]))
                position += 1

            if not window:
                await asyncio.sleep(0)
                continue

            try:
                groups, effective_speed = await loop.run_in_executor(
                    _synthesis_pool,
                    self._call_kokoro_many,
                    [text for _, text in window],
                    voice,
                    speed,
                )
            except Exception:
                # A failed round trip leaves those sentences uncached and says so
                # once, rather than aborting the burst or, worse, touching live
                # playback. The next window continues from where this one stopped.
                logger.warning(
                    "Remote prefetch batch failed for sentences %s",
                    [idx for idx, _ in window],
                    exc_info=True,
                )
                synthesized += len(window)
                await asyncio.sleep(0)
                continue

            # One returned group per input, in order (`synthesize_many` raises
            # rather than returning a short list), each written under its own
            # sentence key so the cache contract is exactly the serial path's.
            for (idx, text), group in zip(window, groups):
                try:
                    seconds = self._store_group(group, text, voice, effective_speed)
                except Exception:
                    logger.warning(
                        "Remote prefetch failed to cache sentence %s", idx, exc_info=True,
                    )
                    continue
                if seconds is None:
                    continue
                buffered_seconds += seconds
                measured_seconds.append(seconds)
            synthesized += len(window)
            await asyncio.sleep(0)
