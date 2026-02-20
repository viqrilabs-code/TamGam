# app/jobs/embed_book.py

import io
import logging
import traceback
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.book import Book
from app.models.ai import ContentEmbedding
from app.services.gemini_key_manager import generate_embedding_with_fallback

logger = logging.getLogger("tamgam.embed_book")


def extract_text_from_pdf(file_bytes: bytes) -> tuple[str, int]:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        result = "\n\n".join(pages)
        logger.info(f"PDF extraction: {len(reader.pages)} pages, {len(result)} chars")
        return result, len(reader.pages)
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}\n{traceback.format_exc()}")
        raise ValueError(f"PDF extraction failed: {e}")


def extract_text_from_docx(file_bytes: bytes) -> tuple[str, int]:
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)
        page_count = max(1, len(text.split()) // 500)
        logger.info(f"DOCX extraction: {len(paragraphs)} paragraphs, ~{page_count} pages")
        return text, page_count
    except Exception as e:
        logger.error(f"DOCX extraction failed: {e}\n{traceback.format_exc()}")
        raise ValueError(f"DOCX extraction failed: {e}")


def extract_text_from_txt(file_bytes: bytes) -> tuple[str, int]:
    text = file_bytes.decode("utf-8", errors="replace").strip()
    page_count = max(1, len(text.split()) // 500)
    logger.info(f"TXT extraction: {len(text)} chars, ~{page_count} pages")
    return text, page_count


def extract_text(file_bytes: bytes, filename: str) -> tuple[str, int]:
    fname = filename.lower()
    logger.info(f"Extracting text from '{filename}' ({len(file_bytes)} bytes)")
    if fname.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    elif fname.endswith(".docx"):
        return extract_text_from_docx(file_bytes)
    elif fname.endswith(".txt"):
        return extract_text_from_txt(file_bytes)
    else:
        raise ValueError(f"Unsupported file type: {filename}")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += chunk_size - overlap
    return chunks


def embed_book(book_id: UUID, file_bytes: bytes, db: Session, force: bool = False) -> dict:
    logger.info(f"=== embed_book START: book_id={book_id}, file_size={len(file_bytes)} bytes ===")

    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise ValueError(f"Book {book_id} not found")

    logger.info(f"Book: title='{book.title}', filename='{book.filename}'")
    book.embed_status = "processing"
    book.embed_error = None
    db.commit()

    try:
        full_text, page_count = extract_text(file_bytes, book.filename)
        if not full_text.strip():
            raise ValueError("No text could be extracted from the file.")

        logger.info(f"Extraction OK: {page_count} pages, {len(full_text.split())} words")
        book.page_count = page_count

        if force:
            deleted = db.query(ContentEmbedding).filter(ContentEmbedding.book_id == book_id).delete()
            logger.info(f"Deleted {deleted} existing chunks (force=True)")
            db.flush()
        else:
            existing = db.query(ContentEmbedding).filter(ContentEmbedding.book_id == book_id).count()
            if existing > 0:
                logger.info(f"Already embedded ({existing} chunks), skipping")
                book.embed_status = "completed"
                db.commit()
                return {"chunk_count": existing, "status": "skipped"}

        chunks = chunk_text(full_text, chunk_size=500, overlap=50)
        logger.info(f"Chunking OK: {len(chunks)} chunks")

        embedded_count = 0
        failed_count = 0

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            try:
                embedding = generate_embedding_with_fallback(chunk)
                if embedding:
                    embedding = embedding[:768]
                if embedding is None:
                    failed_count += 1
            except Exception as emb_err:
                logger.error(f"Chunk {i} embedding failed: {emb_err}")
                embedding = None
                failed_count += 1

            ce = ContentEmbedding(
                book_id=book_id,
                subject=book.subject,
                content_type="book_chunk",
                chunk_text=chunk,
                chunk_index=i,
                token_count=len(chunk.split()),
                embedding=embedding,
            )
            db.add(ce)
            embedded_count += 1

            if (i + 1) % 20 == 0:
                db.flush()
                logger.info(f"Progress: {i+1}/{len(chunks)} chunks")

        db.flush()
        book.embed_status = "completed"
        book.chunk_count = embedded_count
        book.embedded_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(f"=== embed_book DONE: {embedded_count} chunks, {failed_count} embedding failures ===")
        return {"chunk_count": embedded_count, "failed_embeddings": failed_count, "status": "completed"}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"=== embed_book FAILED: {error_msg} ===\n{traceback.format_exc()}")
        book.embed_status = "failed"
        book.embed_error = error_msg
        db.commit()
        raise