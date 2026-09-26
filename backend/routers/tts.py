import asyncio
import io
import json
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session, select

import db.database as _db
from db.models import Book, Sentence
from services.tts_engine import TTSEngine, SynthJob, normalize_speed


def _requested_speed(msg: dict) -> float:
    """Validate and normalise the `speed` field of an inbound message.

    A thin wrapper over the shared normaliser so the WebSocket path and the MP3
    export path cannot drift apart in what they accept — they used to. Raises
    ValueError with a client-safe message so the caller can report the problem
    rather than letting a bad value reach Kokoro.
    """
    return normalize_speed(msg.get("speed", 1.0))

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
    # One `speed_unavailable` notice per connection: the ability to honour a
    # requested rate is a property of the engine, not of a sentence, so saying it
    # once is enough and repeating it per sentence would be noise.
    speed_downgrade_notified = False

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
        nonlocal speed_downgrade_notified
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
                
                # Read metadata populated by stream_job (word_timestamps + accurate duration_ms)
                meta = engine_tts._sentence_meta.pop(job.sentence_index, {})
                duration_ms = meta.get("duration_ms", chunk_count * 100)
                word_timestamps = meta.get("word_timestamps", [])

                # Tell the client, once per session, if the rate it asked for is
                # not the rate it is getting. Silently playing 1.0x audio while
                # the UI still shows 1.5x is exactly the kind of pretended
                # capability the handoff's rule 7 forbids; the engine knows the
                # truth but nothing was reporting it, so the capability was
                # computed and discarded. One message per session, not per
                # sentence, because it is a property of the engine and repeating
                # it would be noise. The session_id tag lets the client's existing
                # stale-session filter discard it if the user has since seeked.
                requested_speed = meta.get("requested_speed")
                effective_speed = meta.get("effective_speed")
                if (
                    not speed_downgrade_notified
                    and requested_speed is not None
                    and effective_speed is not None
                    and effective_speed != requested_speed
                ):
                    speed_downgrade_notified = True
                    await websocket.send_text(json.dumps({
                        "type": "speed_unavailable",
                        "requested_speed": requested_speed,
                        "effective_speed": effective_speed,
                        "session_id": session_id,
                    }))

                # Prune this index from the cancelled set so it doesn't leak into
                # the next session (defence in depth — router also clears the set).
                engine_tts.cancelled.discard(job.sentence_index)

                await websocket.send_text(json.dumps({
                    "type": "sentence_end",
                    "index": job.sentence_index,
                    "duration_ms": duration_ms,
                    "word_timestamps": word_timestamps,
                    "session_id": session_id,
                }))

                # Inject 500ms silence between chapters
                current_chapter = sentence_data[job.sentence_index].get("chapter", 0)
                next_idx = job.sentence_index + 1
                if next_idx in sentence_data:
                    next_chapter = sentence_data[next_idx].get("chapter", 0)
                    if next_chapter != current_chapter:
                        silence = np.zeros(12000, dtype=np.float32)  # 500ms @ 24000
                        buf = io.BytesIO()
                        sf.write(buf, silence, 24000, format="WAV", subtype="PCM_16")
                        await websocket.send_bytes(buf.getvalue())
        except (asyncio.CancelledError, WebSocketDisconnect):
            return
        except Exception as exc:
            import logging
            logging.exception("TTS consumer error: %s", exc)
            try:
                await websocket.send_text(json.dumps({"type": "error", "message": str(exc), "session_id": session_id}))
            except Exception:
                pass

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
                session_id = int(msg.get("session_id", 0))
                try:
                    speed = _requested_speed(msg)
                except ValueError as exc:
                    await websocket.send_text(json.dumps({
                        "type": "error", "message": str(exc), "session_id": session_id,
                    }))
                    continue
                await _cancel_and_clear()
                producer_task = asyncio.create_task(_producer(from_index, voice, speed))
                consumer_task = asyncio.create_task(_consumer_with_events(session_id))
                # Warm the cache ahead of the current position. The bound that
                # matters is the audio-time budget inside prefetch (60 s by
                # default); the 50 here is only a hard safety cap, and this
                # comment used to claim 25.
                prefetch_cancel.clear()
                prefetch_task = asyncio.create_task(
                    engine_tts.prefetch(sentence_data, from_index + 1, 50, voice, speed, prefetch_cancel)
                )

            elif action == "prefetch_speed":
                # Warm cache at a new speed without interrupting playback.
                # Triggered immediately when user changes speed (before debounce fires).
                pf_voice = str(msg.get("voice", "af_heart"))
                pf_from = int(msg.get("from_index", 0))
                try:
                    pf_speed = _requested_speed(msg)
                except ValueError:
                    # A malformed speed is not worth interrupting playback over;
                    # the next well-formed message will re-warm the cache.
                    continue
                prefetch_cancel.set()
                if prefetch_task and not prefetch_task.done():
                    prefetch_task.cancel()
                    try:
                        await prefetch_task
                    except BaseException:
                        pass
                prefetch_cancel.clear()
                prefetch_task = asyncio.create_task(
                    engine_tts.prefetch(sentence_data, pf_from, 50, pf_voice, pf_speed, prefetch_cancel)
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
