"""Render-state vocabulary for the process-first architecture (Phase 1b)."""

JOB_IDLE = "idle"
JOB_PENDING = "pending"
JOB_PROCESSING = "processing"
JOB_ERROR = "error"
JOB_CANCELLED = "cancelled"
JOB_STATUSES = (JOB_IDLE, JOB_PENDING, JOB_PROCESSING, JOB_ERROR, JOB_CANCELLED)


def is_readable(book) -> bool:
    """A book is readable iff a complete render exists (ready_generation set)."""
    return book.ready_generation is not None
