import asyncio
import hashlib
import io
from dataclasses import dataclass
from typing import AsyncGenerator, Any

import numpy as np
import soundfile as sf


@dataclass
class SynthJob:
    sentence_index: int
    text: str
    voice: str = "af_heart"


class TTSEngine:
    def __init__(self, kokoro: Any):
        self.kokoro = kokoro
        self.current_voice = "af_heart"
        self.queue: asyncio.Queue[SynthJob] = asyncio.Queue(maxsize=10)
        self.cancelled: set[int] = set()

    def _cache_key(self, text: str, voice: str) -> str:
        return hashlib.sha256(f"{text}:{voice}".encode()).hexdigest()

    async def enqueue(self, job: SynthJob) -> None:
        await self.queue.put(job)

    async def cancel_from(self, sentence_index: int) -> None:
        pending = []
        while not self.queue.empty():
            try:
                pending.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for item in pending:
            self.cancelled.add(item.sentence_index)
        # Mark the target index and anything below next pending as cancelled
        if pending:
            self.cancelled.update(range(sentence_index, pending[-1].sentence_index + 1))

    async def stream_next(self) -> AsyncGenerator[bytes, None]:
        """Pull next job from queue and stream it. Kept for backwards compat."""
        job: SynthJob = await self.queue.get()
        async for chunk in self.stream_job(job):
            yield chunk

    async def stream_job(self, job: SynthJob) -> AsyncGenerator[bytes, None]:
        """Stream a specific job — avoids queue re-enqueue race condition."""
        if job.sentence_index in self.cancelled or self.kokoro is None:
            return

        try:
            results = self.kokoro(job.text, voice=job.voice)
        except TypeError:
            results = self.kokoro(job.text)

        for audio, sample_rate in results:
            if job.sentence_index in self.cancelled:
                return

            audio_data = audio if isinstance(audio, np.ndarray) else np.array(audio)
            chunk_samples = sample_rate // 10  # 100ms chunks

            for start in range(0, len(audio_data), chunk_samples):
                if job.sentence_index in self.cancelled:
                    return
                chunk = audio_data[start:start + chunk_samples]
                buf = io.BytesIO()
                sf.write(buf, chunk, sample_rate, format="WAV", subtype="PCM_16")
                yield buf.getvalue()
