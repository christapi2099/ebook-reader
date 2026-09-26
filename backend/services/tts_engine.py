import asyncio
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import AsyncGenerator, Any

import numpy as np
import soundfile as sf
from sqlmodel import Session

import db.database as _db
from db.models import AudioCache

INT16_MAX = 32767
SAMPLE_RATE = 24000


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
            from misaki.en import G2P
            g2p = G2P()
            _, tokens = g2p(text)
            words = [(t.text, len(t.phonemes)) for t in tokens if t.phonemes]
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
        return hashlib.sha256(f"{text}:{voice}:{speed}".encode()).hexdigest()

    def _call_kokoro(self, text: str, voice: str, speed: float) -> list:
        if self._speed_kwarg_supported is not False:
            try:
                result = self.kokoro(text, voice=voice, speed=speed)
                self._speed_kwarg_supported = True
                return result
            except TypeError:
                import logging
                logging.warning("Kokoro speed= kwarg not supported — falling back to 1.0x for this session")
                self._speed_kwarg_supported = False
        try:
            return self.kokoro(text, voice=voice)
        except TypeError:
            return self.kokoro(text)

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
            if t.phonemes and any(c.isalnum() for c in t.text):
                word_timestamps.append({
                    "word": t.text,
                    "start": round((t.start_ts or 0) + audio_offset, 4),
                    "end": round((t.end_ts or 0) + audio_offset, 4),
                })

        audio_flat = audio_data.flatten() if audio_data.ndim > 1 else audio_data
        return audio_data, audio_offset + len(audio_flat) / SAMPLE_RATE

    def _write_cache_entry(
        self,
        audio_parts: list[np.ndarray],
        word_timestamps: list[dict],
        cache_key: str,
        voice: str,
    ) -> tuple[np.ndarray, int]:
        full_audio = np.concatenate(audio_parts)
        duration_ms = int(len(full_audio) / SAMPLE_RATE * 1000)
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

        cache_key = self._cache_key(job.text, job.voice, job.speed)

        with Session(_db.engine) as session:
            cached = session.get(AudioCache, cache_key)

        if cached is not None:
            word_ts = json.loads(cached.word_timestamps) if cached.word_timestamps else None
            if word_ts is None:
                word_ts = self._proportional_timestamps(job.text, cached.duration_ms)
            self._sentence_meta[job.sentence_index] = {
                "word_timestamps": word_ts,
                "duration_ms": cached.duration_ms,
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

        results = self._call_kokoro(job.text, job.voice, job.speed)
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
            try:
                _, duration_ms = self._write_cache_entry(
                    audio_parts, word_timestamps, cache_key, job.voice,
                )
                self._sentence_meta[job.sentence_index] = {
                    "word_timestamps": word_timestamps,
                    "duration_ms": duration_ms,
                }
            except Exception:
                pass

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
            cache_key = self._cache_key(s["text"], voice, speed)
            with Session(_db.engine) as session:
                already = session.get(AudioCache, cache_key)
            if already is not None:
                synthesized += 1
                await asyncio.sleep(0)
                continue
            try:
                results = self._call_kokoro(s["text"], voice, speed)
                audio_parts: list[np.ndarray] = []
                word_timestamps: list[dict] = []
                audio_offset = 0.0
                for result in results:
                    if cancel.is_set():
                        return
                    _, audio_offset = self._collect_result(
                        result, audio_parts, word_timestamps, audio_offset,
                    )
                if audio_parts:
                    self._write_cache_entry(
                        audio_parts, word_timestamps, cache_key, voice,
                    )
            except Exception:
                pass
            synthesized += 1
            await asyncio.sleep(0)
