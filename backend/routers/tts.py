import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session, select

from db.database import create_engine_and_tables, engine
from db.models import Book, Sentence
from services.tts_engine import TTSEngine, SynthJob

router = APIRouter()

_kokoro: Any = None


def set_kokoro(kokoro: Any) -> None:
    global _kokoro
    _kokoro = kokoro


def _load_sentences(book_id: str) -> dict[int, Sentence] | None:
    """Fetch sentences in a short-lived session — not held open during WebSocket lifetime."""
    with Session(engine) as session:
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

    async def _producer(from_index: int, voice: str) -> None:
        for idx in sorted(sentence_data.keys()):
            s = sentence_data[idx]
            if idx < from_index or s["filtered"]:
                continue
            await engine_tts.enqueue(SynthJob(
                sentence_index=idx,
                text=s["text"],
                voice=voice,
            ))

    async def _consumer_with_events() -> None:
        while True:
            if engine_tts.queue.empty() and producer_task and producer_task.done():
                await websocket.send_text(json.dumps({"type": "complete"}))
                break

            try:
                job = await asyncio.wait_for(engine_tts.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if job.sentence_index in engine_tts.cancelled:
                continue

            await websocket.send_text(
                json.dumps({"type": "sentence_start", "index": job.sentence_index})
            )

            duration_ms = 0
            # Pass job directly to avoid race condition from re-enqueue
            async for chunk in engine_tts.stream_job(job):
                duration_ms += 100
                await websocket.send_bytes(chunk)

            # Prune cancelled set to prevent unbounded growth
            engine_tts.cancelled.discard(job.sentence_index)

            await websocket.send_text(
                json.dumps({
                    "type": "sentence_end",
                    "index": job.sentence_index,
                    "duration_ms": duration_ms,
                })
            )

    async def _cancel_and_clear(from_index: int = 0) -> None:
        if producer_task and not producer_task.done():
            producer_task.cancel()
        if consumer_task and not consumer_task.done():
            consumer_task.cancel()
        await engine_tts.cancel_from(from_index)
        engine_tts.cancelled.clear()

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            if action == "play":
                from_index = int(msg.get("from_index", 0))
                voice = str(msg.get("voice", "af_heart"))
                await _cancel_and_clear()
                producer_task = asyncio.create_task(_producer(from_index, voice))
                consumer_task = asyncio.create_task(_consumer_with_events())

            elif action == "seek":
                to_index = int(msg.get("to_index", 0))
                voice = str(msg.get("voice", "af_heart"))
                await _cancel_and_clear(to_index)  # now clears cancelled set too
                producer_task = asyncio.create_task(_producer(to_index, voice))
                consumer_task = asyncio.create_task(_consumer_with_events())

            elif action == "pause":
                if producer_task and not producer_task.done():
                    producer_task.cancel()
                if consumer_task and not consumer_task.done():
                    consumer_task.cancel()

    except WebSocketDisconnect:
        pass
    finally:
        if producer_task and not producer_task.done():
            producer_task.cancel()
        if consumer_task and not consumer_task.done():
            consumer_task.cancel()
