"""Folder endpoints: user-created groupings of library books.

Deleting a folder never deletes books. `Book.folder_id` is nullable and is
cleared instead, so a delete is always safe, and the response reports how many
books were unfiled so the UI can offer an undo.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from db.database import get_session
from db.models import FOLDER_NAME_MAX_LENGTH, Book, Folder

router = APIRouter(prefix="/folders")

NAME_REQUIRED = "Folder name is required"
NAME_TOO_LONG = f"Folder name must be at most {FOLDER_NAME_MAX_LENGTH} characters"
NAME_TAKEN = "A folder with that name already exists"


class FolderCreate(BaseModel):
    name: str


class FolderRename(BaseModel):
    name: str


def _clean_name(raw: str) -> str:
    """Validate and normalise a folder name, or raise 400.

    Whitespace is trimmed rather than rejected, so a name pasted with a stray
    newline still works. An all-whitespace name is empty after trimming and is
    therefore rejected as blank.
    """
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail=NAME_REQUIRED)
    if len(name) > FOLDER_NAME_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=NAME_TOO_LONG)
    return name


def _find_by_name(session: Session, name: str, exclude_id: int | None = None):
    """Case-insensitive lookup, mirroring the column's NOCASE collation."""
    statement = select(Folder).where(func.lower(Folder.name) == name.lower())
    if exclude_id is not None:
        statement = statement.where(Folder.id != exclude_id)
    return session.exec(statement).first()


def _book_counts(session: Session) -> dict[int, int]:
    """Book count per folder, in one query rather than one query per folder."""
    rows = session.exec(
        select(Book.folder_id, func.count(Book.id))
        .where(Book.folder_id.is_not(None))
        .where(Book.ephemeral == False)  # noqa: E712 - SQLAlchemy needs the operator
        .group_by(Book.folder_id)
    ).all()
    return {folder_id: count for folder_id, count in rows}


def _serialize(folder: Folder, book_count: int) -> dict:
    return {
        "id": folder.id,
        "name": folder.name,
        "created_at": folder.created_at,
        "book_count": book_count,
    }


def _require(session: Session, folder_id: int) -> Folder:
    folder = session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


@router.get("")
def list_folders(session: Session = Depends(get_session)):
    counts = _book_counts(session)
    folders = session.exec(select(Folder).order_by(Folder.name)).all()
    return [_serialize(folder, counts.get(folder.id, 0)) for folder in folders]


@router.post("", status_code=201)
def create_folder(body: FolderCreate, session: Session = Depends(get_session)):
    name = _clean_name(body.name)
    if _find_by_name(session, name):
        raise HTTPException(status_code=409, detail=NAME_TAKEN)
    folder = Folder(name=name, created_at=datetime.now(timezone.utc))
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return _serialize(folder, 0)


@router.patch("/{folder_id}")
def rename_folder(
    folder_id: int,
    body: FolderRename,
    session: Session = Depends(get_session),
):
    folder = _require(session, folder_id)
    name = _clean_name(body.name)
    if _find_by_name(session, name, exclude_id=folder_id):
        raise HTTPException(status_code=409, detail=NAME_TAKEN)
    folder.name = name
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return _serialize(folder, _book_counts(session).get(folder.id, 0))


@router.delete("/{folder_id}")
def delete_folder(folder_id: int, session: Session = Depends(get_session)):
    """Delete a folder and unfile its books; the books themselves are kept."""
    folder = _require(session, folder_id)
    unfiled = 0
    for book in session.exec(select(Book).where(Book.folder_id == folder_id)):
        book.folder_id = None
        session.add(book)
        unfiled += 1
    session.delete(folder)
    session.commit()
    return {"ok": True, "unfiled_books": unfiled}
