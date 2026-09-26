from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String
from sqlmodel import SQLModel, Field

# Longest folder name the API accepts. The handoff says the prototype caps this,
# but the prototype source is not in this repo, so the exact figure could not be
# read; 60 is our choice. Defined once so the column and the validator cannot
# drift apart, and declared generously ahead of a NOCASE unique index.
FOLDER_NAME_MAX_LENGTH = 60


class Folder(SQLModel, table=True):
    """A user-created grouping of library books.

    `name` is unique case-insensitively. The NOCASE collation gives SQLite that
    guarantee for rows written by anything, including a script that bypasses the
    API; the router also checks explicitly so it can answer 409 with a useful
    message instead of leaking an IntegrityError.

    Deleting a folder never deletes books: `Book.folder_id` is nullable and is
    cleared instead, so a delete is safe and reversible.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(
        sa_column=Column(
            String(FOLDER_NAME_MAX_LENGTH, collation="NOCASE"),
            unique=True,
            nullable=False,
        )
    )
    created_at: datetime


class Book(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: str
    author: Optional[str] = None
    file_path: str
    file_type: str
    page_count: int
    cover_page: int = 0
    created_at: datetime
    last_opened: Optional[datetime] = None
    ephemeral: bool = False
    # NULL means "not filed"; books are never required to belong to a folder.
    folder_id: Optional[int] = Field(default=None, foreign_key="folder.id", index=True)


class Sentence(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: str = Field(foreign_key='book.id', index=True)
    index: int = Field(index=True)
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    filtered: bool = False
    words: Optional[str] = Field(default=None)
    chapter: int = 0
    chapter_title: Optional[str] = None

class AudioCache(SQLModel, table=True):
    text_hash: str = Field(primary_key=True)
    audio_data: bytes
    duration_ms: int
    voice: str
    word_timestamps: Optional[str] = None
    created_at: datetime

class Progress(SQLModel, table=True):
    book_id: str = Field(primary_key=True, foreign_key='book.id')
    sentence_index: int
    updated_at: datetime


class Bookmark(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    book_id: str = Field(foreign_key='book.id', index=True)
    sentence_index: int
    page: int
    label: str
    created_at: datetime


class MP3Export(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    book_id: str = Field(foreign_key='book.id', index=True)
    voice: str
    speed: float
    # The rate the audio was ACTUALLY rendered at, kept separately from the
    # requested `speed` so the exports list can never advertise a tempo the file
    # does not have. NULL for rows written before this column existed, and for
    # exports still in progress.
    effective_speed: float | None = None
    status: str
    progress: int = 0
    file_path: str | None = None
    file_size: int | None = None
    error_message: str | None = None
    created_at: datetime
    # Which stage the work is in, in the shared vocabulary from
    # services/engine_manager.py (starting / warming_up / processing / encoding /
    # complete). `status` stays pending/processing/done/error so existing clients
    # keep working; `phase` is the finer-grained view the UI shows for Modal
    # work, whose slow part — container boot plus model load — used to be
    # completely invisible. NULL for rows written before this column.
    phase: str | None = None
    # Batch progress, for exports fanned out over Modal containers.
    batches_done: int = 0
    batches_total: int = 0
    # Output format and the options it was rendered with, so a finished export
    # can be described (and re-downloaded with the right extension and content
    # type) without re-deriving them. `options` is JSON.
    format: str = "mp3"
    bitrate_kbps: int | None = None
    options: str | None = None

class UserSettings(SQLModel, table=True):
    id: int = Field(primary_key=True, default=1)
    last_book_id: str | None = Field(foreign_key='book.id', nullable=True)
    last_sentence_index: int = 0
    highlight_enabled: bool = Field(default=True)
    # The synthesis engine the user last chose in Settings ("cpu" | "gpu" |
    # "modal"). NULL means "never chosen", in which case KOKORO_BACKEND decides.
    tts_engine: str | None = None
