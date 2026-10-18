"""Render job state transitions (process-first Phase 1b): claim/lease, triggers,
supersede/cancel checks. Pure DB logic, no HTTP and no synthesis.

Every function is its own SHORT transaction on ``_db.engine`` (resolved at call
time). ``now`` is a UTC datetime (naive is taken as UTC), injectable for tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, or_, select, update

import db.database as _db
from db.models import Book
from services.render_state import (
    JOB_CANCELLED, JOB_IDLE, JOB_PENDING, JOB_PROCESSING,
)

CONTINUE = "continue"
SUPERSEDED = "superseded"
CANCELLED = "cancelled"
GONE = "gone"

_B = Book.__table__.c


def _now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)


def claim_next(worker_id: str, now: Optional[datetime] = None,
               lease_s: int = 120, debounce_s: int = 60) -> Optional[str]:
    """Atomically claim the next job; return its book id or None.

    Candidates: pending rows past the debounce window, plus processing rows
    whose lease has EXPIRED (never live ones). The UPDATE re-asserts the same
    predicate, so a racing worker gets rowcount 0 and moves on.
    """
    now = _now(now)
    claimable = or_(
        and_(_B.job_status == JOB_PENDING,
             or_(_B.pending_since.is_(None),
                 _B.pending_since <= now - timedelta(seconds=debounce_s))),
        and_(_B.job_status == JOB_PROCESSING,
             or_(_B.lease_expires.is_(None), _B.lease_expires <= now)),
    )
    with _db.engine.connect() as conn:
        ids = conn.execute(
            select(_B.id).where(claimable)
            .order_by(_B.render_priority.desc(), _B.created_at.asc())
        ).scalars().all()
    for bid in ids:
        with _db.engine.begin() as conn:
            res = conn.execute(
                update(Book.__table__).where(_B.id == bid, claimable).values(
                    job_status=JOB_PROCESSING, lease_owner=worker_id,
                    lease_expires=now + timedelta(seconds=lease_s),
                    pending_since=None, error_message=None)
            )
        if res.rowcount == 1:
            return bid
    return None


def _update(book_id: str, where, **values) -> bool:
    with _db.engine.begin() as conn:
        res = conn.execute(
            update(Book.__table__).where(_B.id == book_id, *where).values(**values))
    return res.rowcount == 1


def renew_lease(book_id: str, worker_id: str, now: Optional[datetime] = None,
                lease_s: int = 120) -> bool:
    """Extend our lease; False if we no longer own the job."""
    return _update(book_id, [_B.lease_owner == worker_id, _B.job_status == JOB_PROCESSING],
                   lease_expires=_now(now) + timedelta(seconds=lease_s))


def release(book_id: str, worker_id: str) -> bool:
    """Drop our lease without changing job_status (the caller sets the outcome)."""
    return _update(book_id, [_B.lease_owner == worker_id],
                   lease_owner=None, lease_expires=None)


def requeue(book_id: str, worker_id: str) -> bool:
    """Give a superseded job back to the queue (immediately claimable)."""
    return _update(book_id, [_B.lease_owner == worker_id, _B.job_status == JOB_PROCESSING],
                   job_status=JOB_PENDING, lease_owner=None, lease_expires=None,
                   progress=0.0, phase=None)


def mark_progress(book_id: str, progress: float, phase: Optional[str]) -> bool:
    """False if the row is gone (deleted mid-render): the worker must stop."""
    return _update(book_id, [], progress=progress, phase=phase)


def bump_priority(book_id: str) -> bool:
    """Opening a book moves it to the front: one above the current maximum."""
    with _db.engine.begin() as conn:
        top = conn.execute(select(_B.render_priority).order_by(
            _B.render_priority.desc()).limit(1)).scalar()
        res = conn.execute(update(Book.__table__).where(_B.id == book_id)
                           .values(render_priority=(top or 0) + 1))
    return res.rowcount == 1


def request_render(book_id: str, target_fingerprint: str,
                   now: Optional[datetime] = None, debounce: bool = True) -> str:
    """Trigger: want ``target_fingerprint`` rendered. The book stays readable on
    its old generation. Returns the resulting job_status, or GONE.

    Target equal to ready_fingerprint (flip-back) cancels instead.
    """
    with _db.engine.begin() as conn:
        r = conn.execute(select(_B.ready_fingerprint, _B.ready_generation)
                         .where(_B.id == book_id)).first()
        if r is None:
            return GONE
        if r.ready_generation is not None and r.ready_fingerprint == target_fingerprint:
            conn.execute(update(Book.__table__).where(_B.id == book_id).values(
                job_status=JOB_CANCELLED, target_fingerprint=target_fingerprint,
                pending_since=None))
            return JOB_CANCELLED
        # A processing job keeps its lease; its worker sees the changed target
        # via check_job and requeues. Only non-processing rows become pending.
        conn.execute(update(Book.__table__).where(
            _B.id == book_id, _B.job_status != JOB_PROCESSING).values(
            job_status=JOB_PENDING, error_message=None,
            pending_since=_now(now) if debounce else None))
        conn.execute(update(Book.__table__).where(_B.id == book_id).values(
            target_fingerprint=target_fingerprint))
        if debounce:
            conn.execute(update(Book.__table__).where(
                _B.id == book_id, _B.job_status == JOB_PENDING).values(
                pending_since=_now(now)))
        return conn.execute(select(_B.job_status).where(_B.id == book_id)).scalar()


def cancel(book_id: str) -> bool:
    """Cancel any pending/processing job; the ready generation is untouched."""
    return _update(book_id, [_B.job_status.in_((JOB_PENDING, JOB_PROCESSING))],
                   job_status=JOB_CANCELLED, lease_owner=None, lease_expires=None,
                   pending_since=None)


def expire_orphan_leases(now: Optional[datetime] = None) -> int:
    """Startup only: every lease was taken by a process that no longer exists, so
    expire them all and let claim_next take the jobs back. Safe because one backend
    process runs the worker; do not call this while another worker is alive."""
    now = _now(now)
    with _db.engine.begin() as conn:
        res = conn.execute(update(Book.__table__).where(
            _B.job_status == JOB_PROCESSING, _B.lease_expires > now).values(
            lease_expires=now))
    return res.rowcount


def check_job(book_id: str, my_target: Optional[str]) -> str:
    """Worker's between-sentence check: CONTINUE | SUPERSEDED | CANCELLED | GONE."""
    with _db.engine.connect() as conn:
        r = conn.execute(select(_B.job_status, _B.target_fingerprint)
                         .where(_B.id == book_id)).first()
    if r is None:
        return GONE
    if r.job_status == JOB_CANCELLED:
        return CANCELLED
    if r.target_fingerprint != my_target:
        return SUPERSEDED
    return CONTINUE
