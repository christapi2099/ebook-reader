"""User settings router for managing global user preferences."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from db.database import get_session
from db.models import UserSettings

router = APIRouter(prefix="/user", tags=["user"])


class UserSettingsUpdate(BaseModel):
    """Schema for updating user settings."""
    last_book_id: Optional[str] = None
    last_sentence_index: Optional[int] = None


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
        return {"last_book_id": None, "last_sentence_index": 0}
    
    return {
        "last_book_id": settings.last_book_id,
        "last_sentence_index": settings.last_sentence_index
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
    
    if existing_settings:
        # Update existing settings - check if fields are explicitly provided
        if settings.last_book_id is not None or "last_book_id" in settings.model_fields_set:
            existing_settings.last_book_id = settings.last_book_id
        if settings.last_sentence_index is not None or "last_sentence_index" in settings.model_fields_set:
            existing_settings.last_sentence_index = settings.last_sentence_index or 0
    else:
        # Create new settings
        existing_settings = UserSettings(
            id=1,
            last_book_id=settings.last_book_id,
            last_sentence_index=settings.last_sentence_index or 0
        )
        session.add(existing_settings)
    
    session.commit()
    session.refresh(existing_settings)
    
    return {"ok": True}
