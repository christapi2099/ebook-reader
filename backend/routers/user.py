"""User settings router for managing global user preferences."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from db.database import get_session
from db.models import Book, UserSettings

router = APIRouter(prefix="/user", tags=["user"])


class UserSettingsUpdate(BaseModel):
    """Schema for updating user settings."""
    last_book_id: Optional[str] = None
    last_sentence_index: Optional[int] = None
    # Generation of last_book_id that last_sentence_index was computed against.
    last_generation: Optional[int] = None
    highlight_enabled: Optional[bool] = None


@router.get("/settings")
def get_user_settings(session: Session = Depends(get_session)):
    """
    Get global user settings.
    Returns the last read book and sentence index.
    """
    statement = select(UserSettings).where(UserSettings.id == 1)
    settings = session.exec(statement).first()
    
    if not settings:
        # Return default settings if none exist
        return {"last_book_id": None, "last_sentence_index": 0,
                "last_generation": None, "highlight_enabled": True}
    
    return {
        "last_book_id": settings.last_book_id,
        "last_sentence_index": settings.last_sentence_index,
        "last_generation": settings.last_generation,
        "highlight_enabled": settings.highlight_enabled,
    }


@router.post("/settings")
def update_user_settings(
    settings: UserSettingsUpdate,
    session: Session = Depends(get_session)
):
    """
    Update global user settings.
    Creates or updates the single settings row (id=1).
    """
    statement = select(UserSettings).where(UserSettings.id == 1)
    existing_settings = session.exec(statement).first()

    # (book_id, generation, index): an index stamped with an older generation is
    # translated onto the ready one; the stored generation is then the ready one.
    if settings.last_sentence_index is not None:
        book_id = settings.last_book_id or (
            existing_settings.last_book_id if existing_settings else None)
        book = session.get(Book, book_id) if book_id else None
        if book is not None and book.ready_generation is not None:
            from routers.library import translate_index
            settings.last_sentence_index = translate_index(
                book.id, settings.last_generation, settings.last_sentence_index,
                book.ready_generation)
            settings.last_generation = book.ready_generation

    if existing_settings:
        # Update existing settings - check if fields are explicitly provided
        if settings.last_book_id is not None or "last_book_id" in settings.model_fields_set:
            existing_settings.last_book_id = settings.last_book_id
        if settings.last_sentence_index is not None or "last_sentence_index" in settings.model_fields_set:
            existing_settings.last_sentence_index = settings.last_sentence_index or 0
            if settings.last_generation is not None:
                existing_settings.last_generation = settings.last_generation
        if settings.highlight_enabled is not None:
            existing_settings.highlight_enabled = settings.highlight_enabled
    else:
        # Create new settings
        existing_settings = UserSettings(
            id=1,
            last_book_id=settings.last_book_id,
            last_sentence_index=settings.last_sentence_index or 0,
            last_generation=settings.last_generation,
            highlight_enabled=settings.highlight_enabled if settings.highlight_enabled is not None else True,
        )
        session.add(existing_settings)
    
    session.commit()
    session.refresh(existing_settings)
    
    return {"ok": True}
