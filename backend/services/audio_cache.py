"""Bounded eviction for the ``AudioCache`` table.

Why this module exists
----------------------
``AudioCache`` is purely additive today: rows are written by
``services.tts_engine._write_cache_entry`` and deleted by nothing anywhere in
the repository. On the developer's database that reached 4,553 rows holding
808,521,600 bytes of PCM inside a 839,905,280-byte file with
``freelist_count = 0`` — page overhead, not fragmentation — and one 50-sentence
prefetch at a new speed (a single slider drag) adds ~50 rows / ~8.9 MB
unconditionally. See ``docs/research/02-synthesis-strategy.md`` §7.

Where the sweep runs, and why not in the write path
---------------------------------------------------
``ebook2audiobook`` is the cautionary tale in §7: it has a 60-day expiry that
only its CLI path runs, so a successful GUI conversion leaves intermediates on
disk forever. An eviction policy wired into one code path is not an eviction
policy. So :func:`sweep_once` is called from ``main.lifespan`` at startup and
:func:`start_periodic_sweep` keeps calling it for the life of the process —
never from ``stream_job`` or ``_write_cache_entry``, which would cover only the
writers we happen to remember and would put a synchronous delete on the hot
path.

What "least recently used" means here — read this before trusting the name
------------------------------------------------------------------------
``AudioCache.created_at`` is written once, when the row is inserted, and nothing
in the codebase ever updates it. A cache *hit* does not touch the row, so a
sentence the user replays every day is exactly as "old" as one that was
synthesized once and never played. This is therefore **insertion-order (FIFO)
eviction, not true LRU**: it is the only recency signal the table has. The
consequence is real and worth stating plainly — the sweep can delete a sentence
the user is about to replay, and it will simply be re-synthesized. Making it
true LRU means adding a ``last_used_at`` column and writing to it on every
cache hit (write amplification on a read path); that is a deliberate follow-up,
not something to smuggle in behind an "LRU" name.

Eviction counts *stored audio bytes* (``SUM(LENGTH(CAST(audio_data AS BLOB)))``),
the same metric §7 measured, not the size of the database file. The file also
carries pages, indices and freelist, so the cap is a cap on retained audio. The
file size is used as a cheap *upper bound* on that metric to skip the exact sum
when the file is already under the cap — a file smaller than the cap cannot hold
more audio than the cap.

``VACUUM`` — deliberately not automatic
---------------------------------------
SQLite does not return freed pages to the OS without ``VACUUM``, so a sweep
shrinks the row count while the file stays ~840 MB. This module does **not**
VACUUM on a timer or after a sweep, because on this database that means
rewriting the whole file while holding a write lock (needs free space roughly
equal to the file size; a failed VACUUM is harmless but reclaims nothing), and
it would run unattended at startup. Instead :func:`vacuum` is an explicit,
documented operation, and every sweep that deletes rows logs how many bytes one
would return (:func:`cache_stats` reports the same number as
``freelist_bytes``).

Configuration
-------------
``AUDIO_CACHE_MAX_MB``
    Eviction cap in MB, fractions allowed. Default :data:`DEFAULT_MAX_MB`
    (4096). A missing, unparsable, zero or negative value falls back to the
    default — never to "unbounded" and never to "evict everything".
``AUDIO_CACHE_SWEEP_INTERVAL_SECONDS``
    Seconds between periodic sweeps. Default
    :data:`DEFAULT_SWEEP_INTERVAL_SECONDS` (900 = 15 minutes). Same fallback.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from typing import Any, Mapping

from sqlalchemy import delete, text
from sqlmodel import Session

from db.models import AudioCache

logger = logging.getLogger(__name__)

MB = 1024 * 1024

# Default cap: 4096 MB (4 GiB). The developer's database already holds ~808 MB
# of audio (4,553 rows, measured in §7), so any default at or below that would
# delete the user's existing cache on the first boot after this change — a
# destructive surprise from what is meant to be a durability win. 4 GiB is ~5x
# the current payload: years of headroom at the measured ~8.9 MB per slider
# drag, still small enough that the table cannot quietly take over the disk
# (521 GB were free where this was developed), and it is a number a human can
# read and reason about rather than a derived one.
DEFAULT_MAX_MB = 4096

# 15 minutes: far more often than a 4 GiB cap can be crossed at ~8.9 MB per
# speed change, and rare enough that the sweep's SUM is invisible. Startup also
# sweeps once, so a process that runs for less than the interval still evicts.
DEFAULT_SWEEP_INTERVAL_SECONDS = 900.0

# Committing a delete batch per this many bytes of audio keeps the rollback
# journal bounded: one transaction deleting 800 MB of blobs needs a journal
# about that large before it commits.
DELETE_BATCH_BYTES = 64 * MB

ENV_MAX_MB = "AUDIO_CACHE_MAX_MB"
ENV_SWEEP_INTERVAL = "AUDIO_CACHE_SWEEP_INTERVAL_SECONDS"

# Bytes of retained audio in one row. CAST to BLOB first because SQLite's
# LENGTH() counts characters for TEXT and bytes for BLOB, and this must be bytes
# whatever storage class the row happens to hold.
_AUDIO_BYTES = "COALESCE(LENGTH(CAST(audio_data AS BLOB)), 0)"

# Oldest first, then by primary key so the order is deterministic when two rows
# share a timestamp (it makes eviction reproducible in tests). SQLite walks
# ix_audiocache_created_at for this and sorts only the tie-break.
_OLDEST_FIRST = "ORDER BY created_at ASC, text_hash ASC"


def _positive_number(raw: str | None, var: str) -> float | None:
    """Parse a positive numeric env value, or return ``None`` to mean "use the default".

    An absent variable is silent; a present but useless one (unparsable, zero,
    negative, NaN) is logged, because silently ignoring a deliberate-looking
    setting is how a cap ends up not applying.
    """
    value_text = (raw or "").strip()
    if not value_text:
        return None
    try:
        value = float(value_text)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using the default", var, value_text)
        return None
    if math.isnan(value) or value <= 0:
        logger.warning("%s=%r is not a positive number; using the default", var, value_text)
        return None
    return value


def resolve_max_bytes(env: Mapping[str, str] | None = None) -> int:
    """The eviction cap in bytes, from ``AUDIO_CACHE_MAX_MB``.

    Accepts fractions (``0.5`` is half a megabyte); the value is in MB because
    that is the unit the cap is discussed in. Falls back to
    :data:`DEFAULT_MAX_MB` whenever the variable is absent or not a positive
    number. Zero and negative are treated as typos rather than as a literal cap,
    because ``AUDIO_CACHE_MAX_MB=0`` read literally would delete every row on
    boot. Use a very large number to make the cap ineffective.
    """
    env = os.environ if env is None else env
    megabytes = _positive_number(env.get(ENV_MAX_MB), ENV_MAX_MB)
    if megabytes is None:
        return DEFAULT_MAX_MB * MB
    try:
        return int(megabytes * MB)
    except (OverflowError, ValueError):
        logger.warning("%s=%r is too large; using the default %d MB", ENV_MAX_MB, megabytes, DEFAULT_MAX_MB)
        return DEFAULT_MAX_MB * MB


def resolve_sweep_interval_seconds(env: Mapping[str, str] | None = None) -> float:
    """Seconds between periodic sweeps, from ``AUDIO_CACHE_SWEEP_INTERVAL_SECONDS``.

    Same fallback rule as :func:`resolve_max_bytes`: absent or nonsensical means
    :data:`DEFAULT_SWEEP_INTERVAL_SECONDS`, never "sweep in a tight loop".
    """
    env = os.environ if env is None else env
    seconds = _positive_number(env.get(ENV_SWEEP_INTERVAL), ENV_SWEEP_INTERVAL)
    return DEFAULT_SWEEP_INTERVAL_SECONDS if seconds is None else seconds


def cache_stats(engine, max_bytes: int | None = None) -> dict[str, Any]:
    """Row count, retained audio bytes and the effective cap.

    Shaped like the other small config/status dicts in this codebase
    (``modal_remote.RemoteConfig.describe``) so it can be logged or returned
    from an API unchanged.

    ``bytes`` is stored audio only, the metric §7 measured; ``db_bytes`` is the
    whole file (``page_count * page_size``); ``freelist_bytes`` is the part of
    the file that deleting rows has already freed *inside* it and that only
    :func:`vacuum` returns to the OS.
    """
    cap = resolve_max_bytes() if max_bytes is None else int(max_bytes)
    with Session(engine) as session:
        rows, audio_bytes = session.execute(
            text(f"SELECT COUNT(*), COALESCE(SUM({_AUDIO_BYTES}), 0) FROM audiocache")
        ).one()
        page_size, page_count, freelist = _page_stats(session)

    audio_bytes = int(audio_bytes)
    return {
        "rows": int(rows),
        "bytes": audio_bytes,
        "max_bytes": cap,
        "max_mb": cap // MB,
        "utilization": (audio_bytes / cap) if cap > 0 else 0.0,
        "over_cap": audio_bytes > cap,
        "db_bytes": page_count * page_size,
        "freelist_bytes": freelist * page_size,
    }


def _page_stats(session: Session) -> tuple[int, int, int]:
    """``(page_size, page_count, freelist_count)`` for the connection's database."""
    page_size = int(session.execute(text("PRAGMA page_size")).scalar() or 0)
    page_count = int(session.execute(text("PRAGMA page_count")).scalar() or 0)
    freelist = int(session.execute(text("PRAGMA freelist_count")).scalar() or 0)
    return page_size, page_count, freelist


def _file_bytes(session: Session) -> int:
    """Size of the whole database file, in bytes, from SQLite's own page counters.

    Used only as an upper bound on the retained audio (see :func:`evict_to_cap`);
    it includes pages, indices and free pages, so it is always >= the audio.
    """
    page_size, page_count, _ = _page_stats(session)
    return page_size * page_count


def evict_to_cap(engine, max_bytes: int) -> int:
    """Delete least-recently-used rows until the retained audio fits ``max_bytes``.

    Returns the number of rows deleted (0 when the cache is already within the
    cap, and 0 for an empty table).

    "Least recently used" is ``created_at`` ascending — see the module docstring:
    that is insertion order, not true LRU, because a cache hit never updates the
    row. A row is deleted only once the bytes it holds are needed to get back
    under the cap, so the sweep stops as soon as the retained total is within
    the cap and the boundary case (retained exactly ``max_bytes``) deletes
    nothing.

    The plan is built from a snapshot of the table, then applied in batches.
    A writer that inserts concurrently is not blocked and its rows are not
    deleted; the cache can therefore sit slightly above the cap until the next
    sweep, which is the safe direction.

    Raises on database errors — :func:`sweep_once` is the layer that decides a
    failure is survivable.
    """
    cap = int(max_bytes)

    with Session(engine) as session:
        # Cheap upper bound first. The table's audio cannot be larger than the
        # file that stores it, so a file already under the cap proves the cache
        # is under the cap without reading ~800 MB of blobs merely to add up
        # their lengths — which is what makes the sweep free on almost every
        # run, including the one at startup. Only a file that has outgrown the
        # cap pays for exact accounting.
        if _file_bytes(session) <= cap:
            return 0

        total = int(
            session.execute(
                text(f"SELECT COALESCE(SUM({_AUDIO_BYTES}), 0) FROM audiocache")
            ).scalar()
            or 0
        )

    # Under the cap by the exact count: one scan, no writes, no ORDER BY, no
    # index walk, no journal.
    if total <= cap:
        return 0

    excess = total - cap
    deleted_rows = 0
    deleted_bytes = 0
    batch: list[str] = []
    batch_bytes = 0

    with Session(engine) as session:
        oldest_first = session.execute(
            text(f"SELECT text_hash, {_AUDIO_BYTES} AS audio_bytes FROM audiocache {_OLDEST_FIRST}")
        ).all()
        for text_hash, audio_bytes in oldest_first:
            if deleted_bytes >= excess:
                break
            size = int(audio_bytes or 0)
            batch.append(text_hash)
            batch_bytes += size
            deleted_bytes += size
            if batch_bytes >= DELETE_BATCH_BYTES:
                deleted_rows += _delete_batch(session, batch)
                batch, batch_bytes = [], 0
        if batch:
            deleted_rows += _delete_batch(session, batch)

    return deleted_rows


def _delete_batch(session: Session, text_hashes: list[str]) -> int:
    """Delete one batch by primary key and commit it, returning the rows removed."""
    result = session.execute(
        delete(AudioCache).where(AudioCache.text_hash.in_(text_hashes))
    )
    session.commit()
    return int(result.rowcount or 0)


def vacuum(engine) -> None:
    """Rewrite the database file, returning freed pages to the OS.

    Explicit and manual by design — never called by the sweep or the periodic
    task. The costs are real:

    * SQLite rewrites the **whole file**, and needs free space roughly equal to
      its current size; with less, the VACUUM fails and reclaims nothing (the
      database itself is unharmed).
    * It holds a **write lock for the entire rewrite**, so on the ~840 MB
      database every reader and writer in the app stalls for the duration
      (books, progress, exports — not just the cache).
    * It rebuilds every index, so the new ``ix_audiocache_created_at`` is
      rebuilt too.

    Run it deliberately — after a sweep that deleted a lot, or from a
    maintenance script — and never while the user is reading. ``cache_stats
    (engine)["freelist_bytes"]`` is how much space it would return.
    """
    # VACUUM cannot run inside a transaction, hence AUTOCOMMIT on a connection
    # taken straight from the engine (not a Session, which always opens one).
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM"))


async def sweep_once(engine, max_bytes: int | None = None) -> int:
    """Run one eviction sweep off the event loop; returns the rows deleted.

    This is the single place every caller goes through (startup and the
    periodic task), so logging and failure handling cannot drift between them.
    A database error is logged and swallowed: a failed sweep must not take the
    reader down, and the next sweep retries.
    """
    cap = resolve_max_bytes() if max_bytes is None else int(max_bytes)
    try:
        # to_thread, not a direct call: the sweep reads and writes SQLite, and
        # the event loop has audio streaming on it.
        deleted = await asyncio.to_thread(evict_to_cap, engine, cap)
    except Exception:
        logger.exception("[audio-cache] eviction sweep failed; cache left as it was")
        return 0

    if not deleted:
        return 0

    try:
        stats = await asyncio.to_thread(cache_stats, engine, cap)
    except Exception:  # stats are for the log line only; never fail the sweep for them
        logger.exception("[audio-cache] sweep deleted rows but stats could not be read")
        logger.info("[audio-cache] evicted %d rows", deleted)
        return deleted

    logger.info(
        "[audio-cache] evicted %d rows; %d rows / %.1f MB of a %d MB cap "
        "(file %.1f MB, %.1f MB reclaimable by VACUUM)",
        deleted,
        stats["rows"],
        stats["bytes"] / MB,
        stats["max_mb"],
        stats["db_bytes"] / MB,
        stats["freelist_bytes"] / MB,
    )
    return deleted


async def periodic_sweep(
    engine,
    *,
    max_bytes: int | None = None,
    interval_seconds: float | None = None,
) -> None:
    """Sweep every ``interval_seconds`` until the task is cancelled.

    Sleeps *first*: startup already sweeps once, and a process that only lives a
    few seconds should not sweep twice. Cancellation is the normal exit — the
    caller cancels the task at shutdown (see :func:`stop_periodic_sweep`).
    """
    cap = resolve_max_bytes() if max_bytes is None else int(max_bytes)
    interval = (
        resolve_sweep_interval_seconds()
        if interval_seconds is None
        else float(interval_seconds)
    )
    logger.info(
        "[audio-cache] eviction enabled: cap %d MB, sweeping every %.0f s",
        cap // MB,
        interval,
    )
    while True:
        await asyncio.sleep(interval)
        await sweep_once(engine, cap)


def start_periodic_sweep(
    engine,
    *,
    max_bytes: int | None = None,
    interval_seconds: float | None = None,
) -> asyncio.Task:
    """Start :func:`periodic_sweep` as a background task and return it.

    The caller owns the task and must cancel it on shutdown; ``main.lifespan``
    does. Named so it is identifiable in an asyncio task dump.
    """
    return asyncio.create_task(
        periodic_sweep(
            engine, max_bytes=max_bytes, interval_seconds=interval_seconds
        ),
        name="audio-cache-eviction",
    )


async def stop_periodic_sweep(task: asyncio.Task | None) -> None:
    """Cancel a sweep task and wait for it to finish. No-op for ``None``."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
