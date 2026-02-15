# app/jobs/embed_content.py
# Content embedding pipeline for RAG (Retrieval Augmented Generation)
#
# Chunks text from transcripts and notes, generates 768-dim embeddings
# using Gemini text-embedding-004, stores in content_embeddings table.
#
# In dev mode (no Vertex AI): stores chunks WITHOUT embeddings.
# pgvector search still works but returns random order (no similarity).
# Real embeddings activate automatically when GCP is configured.
#
# Chunking strategy:
#   Transcripts: 500-word chunks with 50-word overlap
#   Notes: summary + each key_point + detailed_notes sections

import re
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.ai import ContentEmbedding
from app.models.note import Note
from app.models.transcript import Transcript


# ── Text Chunking ─────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """
    Split text into overlapping word-based chunks.

    Args:
        text: Input text
        chunk_size: Target words per chunk
        overlap: Words of overlap between consecutive chunks

    Returns:
        List of text chunks
    """
    words = text.split()
    if not words:
        return []

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end == len(words):
            break
        start += chunk_size - overlap

    return chunks


def chunk_notes(note_content: dict) -> List[str]:
    """
    Extract meaningful chunks from structured note content.
    Each chunk is semantically complete (not mid-sentence splits).

    Returns list of text chunks ready for embedding.
    """
    chunks = []

    # Summary as its own chunk
    summary = note_content.get("summary", "").strip()
    if summary:
        chunks.append(f"Summary: {summary}")

    # Each key point as its own chunk
    for point in note_content.get("key_points", []):
        point = point.strip()
        if point:
            chunks.append(f"Key point: {point}")

    # Detailed notes -- split by heading sections
    detailed = note_content.get("detailed_notes", "")
    if detailed:
        # Split on markdown headings (## or ###)
        sections = re.split(r"\n#+\s+", detailed)
        for section in sections:
            section = section.strip()
            if section and len(section.split()) > 10:
                # Further chunk long sections
                if len(section.split()) > 300:
                    for sub_chunk in chunk_text(section, chunk_size=250, overlap=30):
                        chunks.append(sub_chunk)
                else:
                    chunks.append(section)

    # Q&A pairs
    for qa in note_content.get("qa_pairs", []):
        q = qa.get("question", "").strip()
        a = qa.get("answer", "").strip()
        if q and a:
            chunks.append(f"Q: {q}\nA: {a}")

    return chunks


# ── Embedding Generation ──────────────────────────────────────────────────────

def _get_embedding(text: str) -> Optional[list]:
    """
    Generate embedding for a text chunk.
    Returns 768-dim float list or None if Vertex AI not configured.
    """
    try:
        from app.services.vertex_ai import generate_embedding
        return generate_embedding(text)
    except Exception:
        return None


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def embed_class_content(
    class_id: UUID,
    db: Session,
    force: bool = False,
) -> dict:
    """
    Embed all content for a class (transcript + notes).
    Idempotent -- skips chunks already embedded unless force=True.

    Args:
        class_id: Class UUID
        db: Database session
        force: Delete existing embeddings and re-embed

    Returns:
        dict with transcript_chunks, note_chunks, total_chunks, status
    """
    transcript_chunks_created = 0
    note_chunks_created = 0

    if force:
        db.query(ContentEmbedding).filter(
            ContentEmbedding.class_id == class_id
        ).delete()
        db.flush()

    # ── Embed Transcript ──────────────────────────────────────────────────────
    transcript = db.query(Transcript).filter(
        and_(
            Transcript.class_id == class_id,
            Transcript.status == "completed",
        )
    ).first()

    if transcript and transcript.raw_text:
        # Check not already embedded
        existing = db.query(ContentEmbedding).filter(
            ContentEmbedding.transcript_id == transcript.id
        ).count()

        if existing == 0 or force:
            chunks = chunk_text(transcript.raw_text, chunk_size=500, overlap=50)
            for i, chunk_text_str in enumerate(chunks):
                embedding = _get_embedding(chunk_text_str)
                ce = ContentEmbedding(
                    class_id=class_id,
                    transcript_id=transcript.id,
                    chunk_text=chunk_text_str,
                    chunk_index=i,
                    token_count=len(chunk_text_str.split()),
                    embedding=embedding,
                )
                db.add(ce)
                transcript_chunks_created += 1
            db.flush()

    # ── Embed Notes ───────────────────────────────────────────────────────────
    note = db.query(Note).filter(
        and_(
            Note.class_id == class_id,
            Note.status == "completed",
        )
    ).first()

    if note and note.content:
        existing = db.query(ContentEmbedding).filter(
            ContentEmbedding.note_id == note.id
        ).count()

        if existing == 0 or force:
            content = note.content if isinstance(note.content, dict) else {}
            chunks = chunk_notes(content)
            for i, chunk_text_str in enumerate(chunks):
                embedding = _get_embedding(chunk_text_str)
                ce = ContentEmbedding(
                    class_id=class_id,
                    note_id=note.id,
                    chunk_text=chunk_text_str,
                    chunk_index=i,
                    token_count=len(chunk_text_str.split()),
                    embedding=embedding,
                )
                db.add(ce)
                note_chunks_created += 1
            db.flush()

    db.commit()

    total = transcript_chunks_created + note_chunks_created
    if total == 0:
        status = "skipped"
        message = "No new content to embed (already embedded or no completed transcript/notes)."
    elif transcript_chunks_created > 0 and note_chunks_created > 0:
        status = "completed"
        message = f"Embedded {transcript_chunks_created} transcript chunks and {note_chunks_created} note chunks."
    else:
        status = "partial"
        message = f"Embedded {transcript_chunks_created} transcript chunks and {note_chunks_created} note chunks."

    return {
        "transcript_chunks": transcript_chunks_created,
        "note_chunks": note_chunks_created,
        "total_chunks": total,
        "status": status,
        "message": message,
    }