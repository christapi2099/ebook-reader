import asyncio
import io
import json
import logging
import threading
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session, select

import db.database as _db
from services import engine_manager
from services.sentence_source import load_sentences
from services.tts_engine import SAMPLE_RATE, TTSEngine, SynthJob, normalize_speed

logger = logging.getLogger(__name__)


def _requested_speed(msg: dict) -> float:
    """Validate and normalise the `speed` field of an inbound message.

    A thin wrapper over the shared normaliser so the WebSocket path and the MP3
    export path cannot drift apart in what they accept — they used to. Raises
    ValueError with a client-safe message so the caller can report the problem
    rather than letting a bad value reach Kokoro.
    """
    return normalize_speed(msg.get("speed", 1.0))


def _start_warmup(engine: Any) -> None:
    """Ask the remote GPU to start, without waiting for it.

    Called once per connection, on the first play/seek, because that is the
    moment the reader has declared it is about to want audio: the container, its
    image and its weights can come up while the first sentence is being prepared
    instead of after. ``warmup()`` only *spawns* the call — it does not wait for
    the GPU — so this returns in the time of one Modal API round trip, and it is
    put on a daemon thread anyway so even that round trip never adds a turn of
    latency to the play request, and never occupies the one synthesis worker
    (``tts_engine._synthesis_pool``, ``max_workers=1``) that the audio needs.

    Every failure is swallowed on purpose. ``warmup()`` legitimately raises when
    the live transport has no spawn callable, and a warm-up is an optimisation:
    it must never break playback, and a cold container is still a working one.
    """
    warmup = getattr(engine, "warmup", None)
    if not callable(warmup):
        return

    def run() -> None:
        try:
            warmup()
        except Exception:
            logger.warning("[kokoro] Modal warm-up spawn failed", exc_info=True)

    threading.Thread(target=run, name="kokoro-warmup-spawn", daemon=True).start()


router = APIRouter()

_kokoro: Any = None


def set_kokoro(kokoro: Any) -> None:
    global _kokoro
    _kokoro = kokoro


@router.websocket("/ws/tts/{book_id}")
async def tts_websocket(websocket: WebSocket, book_id: str):
    await websocket.accept()

    sentence_data = load_sentences(book_id)
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
    # Last `engine_status` phase sent for the current session, so the reader is
    # told about a cold GPU once and not on every sentence.
    reported_engine_phase: str | None = None
    # Whether this connection has already asked the remote GPU to start. One spawn
    # per connection: a warm-up costs GPU seconds, and every later play/seek on
    # the same connection would otherwise ask for another one.
    warmup_started = False

    async def _report_engine_phase(session_id: int, force: bool = False) -> None:
        """Tell the client which GPU phase it is waiting on, if any.

        Silent when the live engine is local or the Modal container is already
        warm: a status line that appears when nothing is slow is worse than no
        status line. The session_id tag lets the client's existing stale-session
        filter discard a message that arrives after a seek.
        """
        nonlocal reported_engine_phase
        phase = engine_manager.manager.playback_phase()
        if phase is None:
            reported_engine_phase = engine_manager.PHASE_READY
            return
        if not force and phase == reported_engine_phase:
            return
        reported_engine_phase = phase
        await websocket.send_text(json.dumps({
            "type": "engine_status",
            "phase": phase,
            "session_id": session_id,
        }))

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

                # A cold Modal container is about to make this sentence wait, so
                # say so before the audio rather than leaving the reader staring
                # at a spinner with no explanation. Re-checked here because the
                # phase can move on (starting → warming_up) between the play
                # request and the first chunk; silent once warm.
                try:
                    await _report_engine_phase(session_id)
                except Exception:
                    pass

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
                        # Half a second of silence at the synthesis rate, so the
                        # gap stays 500 ms if the rate ever stops being 24000.
                        silence = np.zeros(SAMPLE_RATE // 2, dtype=np.float32)
                        buf = io.BytesIO()
                        sf.write(buf, silence, SAMPLE_RATE, format="WAV", subtype="PCM_16")
                        await websocket.send_bytes(buf.getvalue())
        except (asyncio.CancelledError, WebSocketDisconnect):
            return
        except Exception as exc:
            logger.exception("TTS consumer error: %s", exc)
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
                # This engine object is built once per WebSocket, so a reader
                # that stays open would otherwise keep synthesising on whatever
                # engine was live when the page loaded — a switch in Settings
                # would only take effect after a reload. The audio cache key
                # (text:voice:speed) is engine-independent, which is correct:
                # same model, same output, whichever device produced it.
                live = engine_manager.manager.current()
                if live is not None:
                    engine_tts.kokoro = live
                # A book has just been opened, which is the one moment a warm-up is
                # worth paying for: ask the remote GPU to start now, so the first
                # sentence does not pay the cold start. `warmup_started` makes this
                # once per connection — a reader that plays, seeks and seeks again
                # must not spawn three times — and the `active()` check makes it a
                # no-op on the local backend, where there is no GPU to start and no
                # GPU time to pay for.
                if not warmup_started and engine_manager.manager.active() == engine_manager.MODAL:
                    warmup_started = True
                    _start_warmup(live)
                reported_engine_phase = None
                await _report_engine_phase(session_id, force=True)
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
