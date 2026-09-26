"""Tests for bounded ``AudioCache`` eviction (``services/audio_cache.py``).

Every test here runs against a throwaway temp-file database built by
``db.database.create_engine_and_tables``, so the schema under test is the real
one — including the ``ix_audiocache_created_at`` index the sweep depends on.
``backend/ebook_reader.db`` is never opened: the autouse guard in ``conftest.py``
already points ``db.database.engine`` at an in-memory database and rewrites
``_DEFAULT_DB`` into ``tmp_path``, so even a mistake here cannot reach the
user's 840 MB file.

What is pinned: the cap is a cap on *stored audio bytes*; eviction is
oldest-first by ``created_at`` (insertion order — see the module docstring, this
is not true LRU); the sweep stops as soon as the retained total is within the
cap; and the default cap cannot delete data that is already on disk.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, create_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import db.database as _db  # noqa: E402
from db.models import AudioCache  # noqa: E402
from services import audio_cache  # noqa: E402

MB = 1024 * 1024
ROW_BYTES = 1000
# The cache measured in docs/research/02-synthesis-strategy.md §7 (read-only,
# 2026-05-13): 4,553 rows holding 808,521,600 bytes inside an 839,917,568-byte
# file. Kept as named constants because two tests exist to protect exactly them.
MEASURED_AUDIO_BYTES = 808_521_600
MEASURED_DB_BYTES = 839_917_568
INDEX_NAME = "ix_audiocache_created_at"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _engine(tmp_path: Path, name: str = "cache.db"):
    """A real-schema database in a temp file (never the user's database)."""
    return _db.create_engine_and_tables(db_url=f"sqlite:///{tmp_path / name}")


def _seed(engine, count: int, size: int = ROW_BYTES, offset: int = 0) -> list[str]:
    """Insert ``count`` rows, oldest first, one minute apart.

    Returns the ``text_hash`` values in insertion (= oldest-first) order. Sizes
    are exact: the payload is ``size`` bytes, which is what the cap counts.
    """
    base = datetime(2026, 1, 1, tzinfo=UTC)
    hashes = [f"hash-{offset + i:03d}" for i in range(count)]
    with Session(engine) as session:
        for position, text_hash in enumerate(hashes):
            session.add(
                AudioCache(
                    text_hash=text_hash,
                    audio_data=b"\x00" * size,
                    duration_ms=1000,
                    voice="af_heart",
                    created_at=base + timedelta(minutes=position),
                )
            )
        session.commit()
    return hashes


def _remaining_hashes(engine) -> list[str]:
    """Every ``text_hash`` still stored, oldest first."""
    with Session(engine) as session:
        return [
            row[0]
            for row in session.execute(
                text("SELECT text_hash FROM audiocache ORDER BY created_at ASC, text_hash ASC")
            )
        ]


def _index_names(engine) -> set[str]:
    with Session(engine) as session:
        return {
            row[1] for row in session.execute(text("PRAGMA index_list(audiocache)"))
        }


# --------------------------------------------------------------------------- #
# Empty table and the under-the-cap no-op
# --------------------------------------------------------------------------- #

def test_empty_table_evicts_nothing(tmp_path):
    engine = _engine(tmp_path)

    assert audio_cache.evict_to_cap(engine, max_bytes=1) == 0
    assert audio_cache.cache_stats(engine, max_bytes=1)["rows"] == 0


def test_under_the_cap_deletes_nothing(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 4)

    # The default cap, which is what a real boot uses: far above 4 KB.
    assert audio_cache.evict_to_cap(engine, max_bytes=audio_cache.DEFAULT_MAX_MB * MB) == 0
    assert len(_remaining_hashes(engine)) == 4


def test_under_the_cap_deletes_nothing_when_the_file_outgrows_the_cap(tmp_path):
    """The exact-count path: a file bigger than the cap can still hold less audio."""
    engine = _engine(tmp_path)
    _seed(engine, 4)
    stats = audio_cache.cache_stats(engine, max_bytes=0)

    # Precondition: the file (pages + index) is indeed larger than the payload,
    # so the file-size short circuit cannot answer and the SUM must run.
    cap = stats["bytes"] + 1
    assert stats["db_bytes"] > cap

    assert audio_cache.evict_to_cap(engine, max_bytes=cap) == 0
    assert len(_remaining_hashes(engine)) == 4


# --------------------------------------------------------------------------- #
# Order, boundary and stop condition
# --------------------------------------------------------------------------- #

def test_evicts_oldest_first_and_stops_as_soon_as_it_is_within_the_cap(tmp_path):
    engine = _engine(tmp_path)
    oldest_to_newest = _seed(engine, 4, size=ROW_BYTES)  # 4000 bytes total

    # 4000 bytes held, 3000 allowed: one row has to go, and not two.
    deleted = audio_cache.evict_to_cap(engine, max_bytes=3 * ROW_BYTES)

    assert deleted == 1
    assert _remaining_hashes(engine) == oldest_to_newest[1:], "the newest rows must survive"
    assert audio_cache.cache_stats(engine)["bytes"] == 3 * ROW_BYTES


def test_respects_the_cap_exactly_at_the_boundary(tmp_path):
    """Retained == cap is inside the cap: nothing is deleted."""
    engine = _engine(tmp_path)
    _seed(engine, 4, size=ROW_BYTES)

    assert audio_cache.evict_to_cap(engine, max_bytes=4 * ROW_BYTES) == 0
    assert len(_remaining_hashes(engine)) == 4


def test_one_byte_over_the_cap_evicts_only_what_is_needed(tmp_path):
    engine = _engine(tmp_path)
    oldest_to_newest = _seed(engine, 4, size=ROW_BYTES)

    deleted = audio_cache.evict_to_cap(engine, max_bytes=4 * ROW_BYTES - 1)

    assert deleted == 1
    assert _remaining_hashes(engine) == oldest_to_newest[1:]


def test_returns_zero_when_already_inside_the_cap_and_deletes_nothing(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 3, size=ROW_BYTES)

    assert audio_cache.evict_to_cap(engine, max_bytes=10 * ROW_BYTES) == 0
    assert len(_remaining_hashes(engine)) == 3


def test_deletes_every_row_when_the_cap_cannot_hold_one(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 4, size=ROW_BYTES)

    assert audio_cache.evict_to_cap(engine, max_bytes=1) == 4
    assert _remaining_hashes(engine) == []


def test_deletes_in_batches_and_still_returns_the_true_row_count(tmp_path, monkeypatch):
    """The byte-bounded batching (which bounds the rollback journal) stays exact."""
    monkeypatch.setattr(audio_cache, "DELETE_BATCH_BYTES", 2 * ROW_BYTES)
    engine = _engine(tmp_path)
    oldest_to_newest = _seed(engine, 5, size=ROW_BYTES)  # 5000 bytes held

    deleted = audio_cache.evict_to_cap(engine, max_bytes=2 * ROW_BYTES)  # keep 2 newest

    assert deleted == 3
    assert _remaining_hashes(engine) == oldest_to_newest[-2:]


def test_second_sweep_is_idempotent(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 4, size=ROW_BYTES)

    assert audio_cache.evict_to_cap(engine, max_bytes=2 * ROW_BYTES) == 2
    after_first = audio_cache.cache_stats(engine)

    assert audio_cache.evict_to_cap(engine, max_bytes=2 * ROW_BYTES) == 0
    assert audio_cache.cache_stats(engine) == after_first
    assert len(_remaining_hashes(engine)) == 2


def test_only_the_oldest_rows_of_a_larger_cache_are_removed(tmp_path, monkeypatch):
    """Scale check: the survivors are exactly the newest rows, in order."""
    monkeypatch.setattr(audio_cache, "DELETE_BATCH_BYTES", 8 * ROW_BYTES)
    engine = _engine(tmp_path)
    oldest_to_newest = _seed(engine, 50, size=ROW_BYTES)

    deleted = audio_cache.evict_to_cap(engine, max_bytes=10 * ROW_BYTES)

    assert deleted == 40
    assert _remaining_hashes(engine) == oldest_to_newest[-10:]


# --------------------------------------------------------------------------- #
# cache_stats
# --------------------------------------------------------------------------- #

def test_cache_stats_reports_rows_bytes_cap_and_free_space(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 3, size=ROW_BYTES)

    stats = audio_cache.cache_stats(engine, max_bytes=2 * ROW_BYTES)

    assert stats["rows"] == 3
    assert stats["bytes"] == 3 * ROW_BYTES
    assert stats["max_bytes"] == 2 * ROW_BYTES
    assert stats["max_mb"] == 2 * ROW_BYTES // MB
    assert stats["over_cap"] is True
    assert stats["utilization"] == pytest.approx(1.5)
    assert stats["db_bytes"] > stats["bytes"], "the file also holds pages and indices"
    assert stats["freelist_bytes"] == 0, "nothing deleted yet"


def test_cache_stats_utilization_defaults_to_zero_when_the_cap_is_zero(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 1)

    assert audio_cache.cache_stats(engine, max_bytes=0)["utilization"] == 0.0


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def test_default_cap_cannot_delete_the_existing_database_on_the_first_boot():
    """The 808 MB already on disk must survive the default configuration.

    A default at or below the measured payload would make the first boot after
    this change destroy the user's cache. See the module docstring for why the
    default is 4096 MB rather than something derived from the measurements.
    """
    default_bytes = audio_cache.resolve_max_bytes({})

    assert default_bytes >= audio_cache.DEFAULT_MAX_MB * MB
    assert default_bytes > MEASURED_AUDIO_BYTES
    assert default_bytes > MEASURED_DB_BYTES


@pytest.mark.parametrize("raw", ["2048", " 2048 "])
def test_cap_reads_the_env_var(raw):
    assert audio_cache.resolve_max_bytes({"AUDIO_CACHE_MAX_MB": raw}) == 2048 * MB


def test_cap_accepts_a_fractional_megabyte():
    assert audio_cache.resolve_max_bytes({"AUDIO_CACHE_MAX_MB": "0.5"}) == MB // 2


@pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "-1", "nan"])
def test_cap_falls_back_to_the_default_for_a_useless_value(raw):
    """Zero and negatives are typos, not literal caps: 0 would evict everything."""
    assert audio_cache.resolve_max_bytes({"AUDIO_CACHE_MAX_MB": raw}) == audio_cache.DEFAULT_MAX_MB * MB


def test_cap_defaults_when_the_env_var_is_absent():
    assert audio_cache.resolve_max_bytes({}) == audio_cache.DEFAULT_MAX_MB * MB


def test_sweep_interval_reads_the_env_var():
    assert audio_cache.resolve_sweep_interval_seconds(
        {"AUDIO_CACHE_SWEEP_INTERVAL_SECONDS": "120"}
    ) == 120.0


@pytest.mark.parametrize("raw", ["", "abc", "0", "-30"])
def test_sweep_interval_falls_back_to_the_default(raw):
    assert (
        audio_cache.resolve_sweep_interval_seconds(
            {"AUDIO_CACHE_SWEEP_INTERVAL_SECONDS": raw}
        )
        == audio_cache.DEFAULT_SWEEP_INTERVAL_SECONDS
    )


def test_sweep_interval_default_is_sane():
    assert audio_cache.DEFAULT_SWEEP_INTERVAL_SECONDS == 900.0


# --------------------------------------------------------------------------- #
# The async layer: startup sweep, periodic task, shutdown
# --------------------------------------------------------------------------- #

def test_sweep_once_evicts_and_reports_the_row_count(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIO_CACHE_MAX_MB", "0.001")  # 1048 bytes
    engine = _engine(tmp_path)
    _seed(engine, 4, size=ROW_BYTES)

    deleted = asyncio.run(audio_cache.sweep_once(engine))

    assert deleted == 3
    assert len(_remaining_hashes(engine)) == 1


def test_sweep_once_takes_an_explicit_cap(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 4, size=ROW_BYTES)

    assert asyncio.run(audio_cache.sweep_once(engine, max_bytes=2 * ROW_BYTES)) == 2


def test_sweep_once_survives_a_broken_database(tmp_path):
    """A failing sweep must not take startup (or the timer) down with it."""
    broken = create_engine(f"sqlite:///{tmp_path / 'missing-dir' / 'nope.db'}")

    assert asyncio.run(audio_cache.sweep_once(broken, max_bytes=1)) == 0


def test_periodic_sweep_evicts_on_its_interval_and_stops_cleanly(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 4, size=ROW_BYTES)

    async def scenario() -> None:
        task = audio_cache.start_periodic_sweep(
            engine, max_bytes=2 * ROW_BYTES, interval_seconds=0.05
        )
        assert task.get_name() == "audio-cache-eviction"
        await asyncio.sleep(0.3)
        assert not task.done(), "the sweeper must keep running until it is cancelled"
        await audio_cache.stop_periodic_sweep(task)
        assert task.cancelled()

    asyncio.run(scenario())

    assert len(_remaining_hashes(engine)) == 2


def test_periodic_sweep_sleeps_first_so_startup_does_not_double_sweep(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, 4, size=ROW_BYTES)

    async def scenario() -> None:
        task = audio_cache.start_periodic_sweep(
            engine, max_bytes=2 * ROW_BYTES, interval_seconds=30
        )
        await asyncio.sleep(0.2)
        assert len(_remaining_hashes(engine)) == 4, "no sweep before the first interval"
        await audio_cache.stop_periodic_sweep(task)

    asyncio.run(scenario())


def test_stop_periodic_sweep_accepts_none():
    asyncio.run(audio_cache.stop_periodic_sweep(None))


def test_startup_sweep_is_wired_into_the_app_lifespan(tmp_path, monkeypatch):
    """The sweep really runs from main.lifespan, not only from the module's tests."""
    import main

    engine = _engine(tmp_path, "startup.db")
    _seed(engine, 4, size=ROW_BYTES)
    monkeypatch.setenv("AUDIO_CACHE_MAX_MB", "0.002")  # 2097 bytes
    monkeypatch.setattr(main, "create_engine_and_tables", lambda db_url=None: engine)
    monkeypatch.setattr(main, "_init_kokoro", lambda: None)

    with TestClient(main.app):
        pass  # entering the context is the startup; leaving it is the shutdown

    assert len(_remaining_hashes(engine)) == 2, "the lifespan did not sweep at startup"


# --------------------------------------------------------------------------- #
# VACUUM: explicit, documented, and the only thing that shrinks the file
# --------------------------------------------------------------------------- #

def test_a_sweep_frees_pages_inside_the_file_and_only_vacuum_returns_them(tmp_path):
    """The reason VACUUM is documented rather than assumed: the file stays put."""
    engine = _engine(tmp_path)
    _seed(engine, 200, size=4000)  # 800 KB of audio

    assert audio_cache.evict_to_cap(engine, max_bytes=40_000) == 190

    after_sweep = audio_cache.cache_stats(engine)
    assert after_sweep["bytes"] <= 40_000
    assert after_sweep["freelist_bytes"] > 0, "deleted rows leave free pages behind"

    audio_cache.vacuum(engine)

    after_vacuum = audio_cache.cache_stats(engine)
    assert after_vacuum["rows"] == after_sweep["rows"]
    assert after_vacuum["bytes"] == after_sweep["bytes"]
    assert after_vacuum["db_bytes"] < after_sweep["db_bytes"], "VACUUM must shrink the file"


# --------------------------------------------------------------------------- #
# The index the sweep needs
# --------------------------------------------------------------------------- #

def test_created_at_index_exists_after_migration(tmp_path):
    engine = _engine(tmp_path, "fresh.db")

    assert INDEX_NAME in _index_names(engine)
    assert {"text_hash", "created_at"} <= _columns(engine)


def test_created_at_index_is_added_to_a_legacy_database(tmp_path):
    """A database written before this change (the user's) gains the index on boot."""
    import sqlite3

    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE audiocache (text_hash TEXT PRIMARY KEY, audio_data BLOB, "
        "duration_ms INTEGER, voice TEXT, created_at TIMESTAMP)"
    )
    conn.execute("INSERT INTO audiocache VALUES ('old', x'00', 1, 'af_heart', '2026-01-01 00:00:00')")
    conn.commit()
    conn.close()

    engine = _db.create_engine_and_tables(db_url=f"sqlite:///{db_file}")
    assert INDEX_NAME in _index_names(engine)

    # Idempotent: re-running _migrate neither duplicates the index nor loses rows.
    _db._migrate(engine)
    _db._migrate(engine)
    assert INDEX_NAME in _index_names(engine)
    assert _remaining_hashes(engine) == ["old"]


def test_the_sweep_orders_by_created_at_through_the_index(tmp_path):
    """The index is not decorative: the sweep's ORDER BY is served by it."""
    engine = _engine(tmp_path)
    _seed(engine, 20, size=ROW_BYTES)

    with Session(engine) as session:
        plan = "\n".join(
            row[-1]
            for row in session.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT text_hash, "
                    "COALESCE(LENGTH(CAST(audio_data AS BLOB)), 0) "
                    "FROM audiocache ORDER BY created_at ASC, text_hash ASC"
                )
            )
        )

    assert INDEX_NAME in plan, plan


def _columns(engine) -> set[str]:
    with Session(engine) as session:
        return {row[1] for row in session.execute(text("PRAGMA table_info(audiocache)"))}
