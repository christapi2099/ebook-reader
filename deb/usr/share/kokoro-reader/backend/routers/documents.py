import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from db.database import get_session
from db.models import Book, Sentence
from services.epub_engine import EPUBEngine
from services.pdf_engine import PDFEngine
from services.text_filter import TextFilter

router = APIRouter(prefix="/documents")
UPLOAD_DIR = Path("uploads")


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    contents = await file.read()
    book_id = hashlib.sha256(contents).hexdigest()

    existing = session.get(Book, book_id)
    if existing:
        count = len(session.exec(select(Sentence).where(Sentence.book_id == book_id)).all())
        return {"book_id": book_id, "sentence_count": count, "already_existed": True}

    ext = (file.filename or "file.pdf").rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "epub"):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use PDF or EPUB.")

    UPLOAD_DIR.mkdir(exist_ok=True)
    file_path = UPLOAD_DIR / f"{book_id}.{ext}"
    file_path.write_bytes(contents)

    tf = TextFilter()

    if ext == "pdf":
        engine = PDFEngine()
        raw = engine.extract_sentences(str(file_path))
        page_count = engine.page_count(str(file_path))
        sentence_objs = [
            Sentence(
                book_id=book_id, index=s.index, text=s.text,
                page=s.page, x0=s.x0, y0=s.y0, x1=s.x1, y1=s.y1,
                filtered=tf.should_filter(s.text),
            )
            for s in raw
        ]
    elif ext == "epub":
        engine = EPUBEngine()
        raw = engine.extract_sentences(str(file_path))
        page_count = max(1, len(raw) // 10)
        sentence_objs = [
            Sentence(
                book_id=book_id, index=s.index, text=s.text,
                page=0, x0=0.0, y0=0.0, x1=0.0, y1=0.0,
                filtered=tf.should_filter(s.text),
            )
            for s in raw
        ]
    else:  # unreachable — kept for safety
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Unsupported file type. Use PDF or EPUB.")

    session.add_all(sentence_objs)
    session.add(Book(
        id=book_id,
        title=file.filename or "Untitled",
        file_path=str(file_path),
        file_type=ext,
        page_count=page_count,
        created_at=datetime.now(timezone.utc),
        last_opened=datetime.now(timezone.utc),
    ))
    session.commit()

    return {"book_id": book_id, "sentence_count": len(sentence_objs), "already_existed": False}


@router.get("/{book_id}/sentences")
def get_sentences(book_id: str, session: Session = Depends(get_session)):
    if not session.get(Book, book_id):
        raise HTTPException(status_code=404, detail="Book not found")
    rows = session.exec(
        select(Sentence).where(Sentence.book_id == book_id).order_by(Sentence.index)
    ).all()
    return [
        {"index": s.index, "text": s.text, "page": s.page,
         "x0": s.x0, "y0": s.y0, "x1": s.x1, "y1": s.y1, "filtered": s.filtered}
        for s in rows
    ]
