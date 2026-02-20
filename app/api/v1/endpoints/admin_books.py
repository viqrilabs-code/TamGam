# app/api/v1/endpoints/admin_books.py

import logging
import threading
import traceback
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.db.session import get_db
from app.models.book import Book
from app.models.ai import ContentEmbedding
from app.models.user import User
from app.schemas.admin_books import (
    BookResponse, BookListResponse, BookUploadResponse,
    EmbedTriggerResponse, KeyStatusResponse, MessageResponse,
)
from app.services.gemini_key_manager import manager as key_manager

router = APIRouter()
logger = logging.getLogger("tamgam.admin_books")

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}
MAX_FILE_SIZE = 50 * 1024 * 1024


def _embed_in_background(book_id: UUID, file_bytes: bytes, force: bool = False):
    from app.jobs.embed_book import embed_book
    from app.db.session import SessionLocal
    logger.info(f"[BG] Starting embed for book_id={book_id}")
    db = SessionLocal()
    try:
        result = embed_book(book_id=book_id, file_bytes=file_bytes, db=db, force=force)
        logger.info(f"[BG] Embed done: {result}")
    except Exception as e:
        logger.error(f"[BG] Embed failed: {e}\n{traceback.format_exc()}")
    finally:
        db.close()


@router.post("/upload", response_model=BookUploadResponse)
async def upload_book(
    file: UploadFile = File(...),
    title: str = Form(...),
    author: Optional[str] = Form(None),
    subject: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    auto_embed: bool = Form(True),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    logger.info(f"Upload: title='{title}', file='{file.filename}', auto_embed={auto_embed}")
    content_type = file.content_type or ""
    if content_type not in ALLOWED_TYPES and not (file.filename or "").endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{content_type}'.")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Max 50 MB.")
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file.")

    book = Book(
        title=title.strip(),
        author=author.strip() if author else None,
        subject=subject.strip() if subject else None,
        description=description.strip() if description else None,
        filename=file.filename,
        file_size=len(file_bytes),
        embed_status="pending",
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    logger.info(f"Book created: id={book.id}")

    if auto_embed:
        t = threading.Thread(target=_embed_in_background, args=(book.id, file_bytes, False), daemon=True)
        t.start()
        logger.info(f"Background thread started: {t.name}")

    return BookUploadResponse(
        id=book.id, title=book.title, filename=book.filename,
        file_size=book.file_size, embed_status=book.embed_status,
        message="Book uploaded. Embedding started." if auto_embed else "Book uploaded.",
    )


@router.get("", response_model=List[BookListResponse])
def list_books(
    subject: Optional[str] = None,
    embed_status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Book)
    if subject:
        query = query.filter(Book.subject == subject)
    if embed_status:
        query = query.filter(Book.embed_status == embed_status)
    books = query.order_by(Book.created_at.desc()).offset(skip).limit(limit).all()
    return [
        BookListResponse(
            id=b.id, title=b.title, author=b.author, subject=b.subject,
            filename=b.filename, file_size=b.file_size, page_count=b.page_count,
            embed_status=b.embed_status, chunk_count=b.chunk_count,
            embed_error=b.embed_error, embedded_at=b.embedded_at, created_at=b.created_at,
        )
        for b in books
    ]


@router.get("/key-status", response_model=KeyStatusResponse)
def get_key_status(current_user: User = Depends(require_admin)):
    statuses = key_manager.status()
    available = sum(1 for k in statuses if k["available"])
    return KeyStatusResponse(keys=statuses, available_count=available, total_count=len(statuses))


@router.get("/{book_id}", response_model=BookResponse)
def get_book(book_id: UUID, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return BookResponse(
        id=book.id, title=book.title, author=book.author, subject=book.subject,
        description=book.description, filename=book.filename, file_size=book.file_size,
        page_count=book.page_count, embed_status=book.embed_status,
        chunk_count=book.chunk_count, embed_error=book.embed_error,
        embedded_at=book.embedded_at, created_at=book.created_at, updated_at=book.updated_at,
    )


@router.post("/{book_id}/embed", response_model=EmbedTriggerResponse)
async def trigger_embed(
    book_id: UUID, force: bool = False,
    current_user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.embed_status == "processing":
        raise HTTPException(status_code=409, detail="Already in progress.")
    return EmbedTriggerResponse(book_id=book_id, message="Re-upload to re-embed.", embed_status=book.embed_status)


@router.delete("/{book_id}", response_model=MessageResponse)
def delete_book(book_id: UUID, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    chunk_count = db.query(ContentEmbedding).filter(ContentEmbedding.book_id == book_id).delete()
    title = book.title
    db.delete(book)
    db.commit()
    logger.info(f"Deleted '{title}' and {chunk_count} chunks")
    return MessageResponse(message=f"Book '{title}' and {chunk_count} chunks deleted.")