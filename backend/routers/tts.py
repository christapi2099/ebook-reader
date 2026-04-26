import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session, select

import db.database as _db
from db.models import Book, Sentence
from services.tts_engine import TTSEngine, SynthJob

router = APIRouter()

_kokoro: Any = None


def set_kokoro(kokoro: Any) -> None:
    global _kokoro
    _kokoro = kokoro


def _load_sentences(book_id: str) -> dict[int, dict] | None:
    """Fetch sentences in a short-lived session — not held open during WebSocket lifetime."""
    with Session(_db.engine) as session:
        book = session.get(Book, book_id)
        if not book:
            return None
        rows = session.exec(
            select(Sentence)
            .where(Sentence.book_id == book_id)
            .order_by(Sentence.index)
        ).all()
        # Detach from session by converting to plain dicts
        return {s.index: s.model_dump() for s in rows}


@router.websocket("/ws/tts/{book_id}")
async def tts_websocket(websocket: WebSocket, book_id: str):
    await websocket.accept()

    sentence_data = _load_sentences(book_id)
    if sentence_data is None:
        await websocket.close(code=4004, reason="Book not found")
        return

    engine_tts = TTSEngine(_kokoro)
    producer_task: asyncio.Task | None = None
    consumer_task: asyncio.Task | None = None
    prefetch_task: asyncio.Task | None = None
    prefetch_cancel = asyncio.Event()

    async def _producer(from_index: int, voice: str, speed: float) -> None:
        for idx in sorted(sentence_data.keys()):
            s = sentence_data[idx]
            if idx < from_index or s["filtered"]:
                continue
            await engine_tts.enqueue(SynthJob(
                sentence_index=idx,
                text=s["text"],
                voice=voice,
                speed=speed,
            ))

    async def _consumer_with_events(session_id: int) -> None:
        """Stream queued synthesis jobs, tagging every client-bound message with
        the session_id the router received from the play/seek action. The client
        rejects messages whose session_id doesn't match its current session, so
        any message that slips out after we've been cancelled is harmlessly
        discarded downstream.
        """
        try:
            while True:
                if engine_tts.queue.empty() and producer_task and producer_task.done():
                    await websocket.send_text(json.dumps({
                        "type": "complete",
                        "session_id": session_id,
                    }))
                    break

                try:
                    job = await asyncio.wait_for(engine_tts.queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                if job.sentence_index in engine_tts.cancelled:
                    continue

                await websocket.send_text(json.dumps({
                    "type": "sentence_start",
                    "index": job.sentence_index,
                    "session_id": session_id,
                }))

                # Track actual audio sample count from Kokoro output for accurate duration
                duration_ms = 0
                chunk_count = 0
                async for chunk in engine_tts.stream_job(job):
                    chunk_count += 1
                    await websocket.send_bytes(chunk)
                # Calculate duration based on chunk count and speed factor
                duration_ms = int(chunk_count * 100 / job.speed)

                # Prune this index from the cancelled set so it doesn't leak into
                # the next session (defence in depth — router also clears the set).
                engine_tts.cancelled.discard(job.sentence_index)

                await websocket.send_text(json.dumps({
                    "type": "sentence_end",
                    "index": job.sentence_index,
                    "duration_ms": duration_ms,
                    "session_id": session_id,
                }))
        except asyncio.CancelledError:
            # Normal cancellation when router starts a new session or the socket closes.
            return

    async def _cancel_and_clear() -> None:
        """Cancel the active producer/consumer, AWAIT their exit, then reset
        per-session state so the next session starts from a clean slate.

        Awaiting is critical: without it the old consumer can still be mid-send
        when the new one starts, interleaving old `sentence_start`/chunks into the
        WebSocket. The client's session_id filter is the last line of defence
        against that, but we minimise the interleave window by serialising here.
        """
        tasks: list[asyncio.Task] = []
        if producer_task and not producer_task.done():
            producer_task.cancel()
            tasks.append(producer_task)
        if consumer_task and not consumer_task.done():
            consumer_task.cancel()
            tasks.append(consumer_task)
        for t in tasks:
            try:
                await t
            except BaseException:
                # Cancellation or any exception from the dying task must never
                # block starting the next session. CancelledError is BaseException
                # in 3.8+, so we catch BaseException here on purpose.
                pass

        # Drain anything the producer managed to enqueue before exiting so the new
        # session doesn't inherit its jobs.
        while not engine_tts.queue.empty():
            try:
                engine_tts.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Cancel any in-progress prefetch so it doesn't race with new synthesis.
        prefetch_cancel.set()
        if prefetch_task and not prefetch_task.done():
            prefetch_task.cancel()
            try:
                await prefetch_task
            except BaseException:
                pass

        # Reset the cancelled set — with the old consumer confirmed exited, no one
        # is reading it, and leaving stale indices in would cause the new
        # session's re-enqueued jobs for the same indices to be silently skipped.
        engine_tts.cancelled.clear()

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            if action in ("play", "seek"):
                if action == "play":
                    from_index = int(msg.get("from_index", 0))
                else:
                    from_index = int(msg.get("to_index", 0))
                voice = str(msg.get("voice", "af_heart"))
                speed = float(msg.get("speed", 1.0))
                session_id = int(msg.get("session_id", 0))
                await _cancel_and_clear()
                producer_task = asyncio.create_task(_producer(from_index, voice, speed))
                consumer_task = asyncio.create_task(_consumer_with_events(session_id))
                # Pre-warm cache for the next 25 sentences after the current position.
                prefetch_cancel.clear()
                prefetch_task = asyncio.create_task(
                    engine_tts.prefetch(sentence_data, from_index + 1, 25, voice, speed, prefetch_cancel)
                )

            elif action == "pause":
                await _cancel_and_clear()

    except WebSocketDisconnect:
        pass
    finally:
        if producer_task and not producer_task.done():
            producer_task.cancel()
        if consumer_task and not consumer_task.done():
            consumer_task.cancel()
