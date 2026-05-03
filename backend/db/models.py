from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field

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

class AudioCache(SQLModel, table=True):
    text_hash: str = Field(primary_key=True)
    audio_data: bytes
    duration_ms: int
    voice: str
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
    status: str
    progress: int = 0
    file_path: str | None = None
    file_size: int | None = None
    error_message: str | None = None
    created_at: datetime
