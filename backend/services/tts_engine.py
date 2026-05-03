import asyncio
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import AsyncGenerator, Any

import numpy as np
import soundfile as sf
from sqlmodel import Session

import db.database as _db
from db.models import AudioCache


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

    def _cache_key(self, text: str, voice: str, speed: float) -> str:
        return hashlib.sha256(f"{text}:{voice}:{speed}".encode()).hexdigest()

    async def enqueue(self, job: SynthJob) -> None:
        await self.queue.put(job)

    async def stream_job(self, job: SynthJob) -> AsyncGenerator[bytes, None]:
        """Stream a specific job, serving from AudioCache when available."""
        if job.sentence_index in self.cancelled or self.kokoro is None:
            return

        sample_rate = 24000
        cache_key = self._cache_key(job.text, job.voice, job.speed)

        with Session(_db.engine) as session:
            cached = session.get(AudioCache, cache_key)

        if cached is not None:
            audio_data = np.frombuffer(cached.audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            chunk_samples = sample_rate // 10
            for start in range(0, len(audio_data), chunk_samples):
                if job.sentence_index in self.cancelled:
                    return
                chunk = audio_data[start:start + chunk_samples]
                buf = io.BytesIO()
                sf.write(buf, chunk, sample_rate, format="WAV", subtype="PCM_16")
                yield buf.getvalue()
            return

        try:
            results = self.kokoro(job.text, voice=job.voice, speed=job.speed)
        except TypeError:
            try:
                results = self.kokoro(job.text, voice=job.voice)
            except TypeError:
                results = self.kokoro(job.text)

        all_audio_parts: list[np.ndarray] = []
        chunk_samples = sample_rate // 10

        for result in results:
            await asyncio.sleep(0)
            audio = result[-1]
            if job.sentence_index in self.cancelled:
                return
            audio_data = audio if isinstance(audio, np.ndarray) else np.array(audio)
            all_audio_parts.append(audio_data)
            for start in range(0, len(audio_data), chunk_samples):
                if job.sentence_index in self.cancelled:
                    return
                chunk = audio_data[start:start + chunk_samples]
                buf = io.BytesIO()
                sf.write(buf, chunk, sample_rate, format="WAV", subtype="PCM_16")
                yield buf.getvalue()

        if all_audio_parts and job.sentence_index not in self.cancelled:
            try:
                full_audio = np.concatenate(all_audio_parts)
                duration_ms = int(len(full_audio) / sample_rate * 1000)
                pcm_bytes = (full_audio * 32768.0).clip(-32768, 32767).astype(np.int16).tobytes()
                entry = AudioCache(
                    text_hash=cache_key,
                    audio_data=pcm_bytes,
                    duration_ms=duration_ms,
                    voice=job.voice,
                    created_at=datetime.now(UTC),
                )
                with Session(_db.engine) as session:
                    if session.get(AudioCache, cache_key) is None:
                        session.add(entry)
                        session.commit()
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
        sample_rate = 24000
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
                results = self.kokoro(s["text"], voice=voice, speed=speed)
                parts: list[np.ndarray] = []
                for result in results:
                    if cancel.is_set():
                        return
                    audio = result[-1]
                    parts.append(audio if isinstance(audio, np.ndarray) else np.array(audio))
                if parts:
                    full = np.concatenate(parts)
                    duration_ms = int(len(full) / sample_rate * 1000)
                    pcm = (full * 32768.0).clip(-32768, 32767).astype(np.int16).tobytes()
                    entry = AudioCache(
                        text_hash=cache_key,
                        audio_data=pcm,
                        duration_ms=duration_ms,
                        voice=voice,
                        created_at=datetime.now(UTC),
                    )
                    with Session(_db.engine) as session:
                        if session.get(AudioCache, cache_key) is None:
                            session.add(entry)
                            session.commit()
            except Exception:
                pass
            synthesized += 1
            await asyncio.sleep(0)
