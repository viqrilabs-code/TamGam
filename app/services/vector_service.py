# app/services/vector_service.py
# Centralized vector operations for Diya's RAG pipeline
#
# Wraps all pgvector interactions in one place:
#   - similarity_search()      — cosine search over content_embeddings
#   - build_index()            — create/refresh HNSW index for fast ANN search
#   - upsert_embedding()       — insert or update a single embedding row
#   - delete_embeddings()      — remove embeddings by source (transcript/note/post)
#   - embedding_stats()        — count and coverage diagnostics per class
#
# Design principles:
#   • All raw SQL uses parameterised queries (no f-string injection).
#   • Falls back gracefully when Vertex AI is unavailable (dev mode).
#   • Returns typed dataclasses — callers never touch raw Row objects.
#   • index build is advisory (does not block the request path).

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """A single chunk returned by similarity_search."""
    chunk_text: str
    content_type: str           # transcript_chunk | note_section | community_post
    chunk_index: Optional[int]
    distance: float             # cosine distance — lower is more similar (0 = identical)
    source_id: Optional[str]    # transcript_id / note_id / post_id (whichever is set)
    class_id: Optional[str]


@dataclass
class EmbeddingStats:
    """Coverage report for a class."""
    class_id: str
    total_chunks: int
    transcript_chunks: int
    note_chunks: int
    community_chunks: int
    chunks_with_embedding: int
    chunks_without_embedding: int

    @property
    def coverage_pct(self) -> float:
        if self.total_chunks == 0:
            return 0.0
        return round(self.chunks_with_embedding / self.total_chunks * 100, 1)

    @property
    def is_ready(self) -> bool:
        """True when all stored chunks have embeddings."""
        return self.total_chunks > 0 and self.chunks_without_embedding == 0


# ── Core similarity search ────────────────────────────────────────────────────

def similarity_search(
    question: str,
    db: Session,
    *,
    class_id: Optional[UUID] = None,
    subject: Optional[str] = None,
    content_types: Optional[List[str]] = None,
    top_k: int = 5,
    max_distance: float = 0.6,   # Reject very dissimilar chunks (cosine distance cap)
) -> List[SearchResult]:
    """
    Search content_embeddings using pgvector cosine similarity.

    Args:
        question:      The student's question — will be embedded on the fly.
        db:            SQLAlchemy session.
        class_id:      Scope search to a single class (recommended for focused Q&A).
        subject:       Further filter by subject string (e.g. "Mathematics").
        content_types: Restrict to specific source types.
                       Defaults to all: ["transcript_chunk", "note_section", "community_post"]
        top_k:         Number of results to return.
        max_distance:  Cosine distance threshold — chunks above this are too dissimilar.

    Returns:
        List of SearchResult, ordered by ascending cosine distance (most relevant first).
        Returns [] if embedding fails (dev mode / GCP not configured).
    """
    embedding = _embed(question)
    if embedding is None:
        log.debug("similarity_search: no embedding available (dev mode), returning []")
        return []

    embedding_str = _format_vector(embedding)
    params: dict = {
        "embedding": embedding_str,
        "top_k": top_k,
        "max_distance": max_distance,
    }

    # ── Build WHERE clauses dynamically ───────────────────────────────────────
    filters = []

    if class_id is not None:
        filters.append("class_id = :class_id")
        params["class_id"] = str(class_id)

    if subject is not None:
        filters.append("subject = :subject")
        params["subject"] = subject

    if content_types:
        # Cast list to a format SQLAlchemy text() can handle with ANY
        placeholders = ", ".join(f":ct_{i}" for i, _ in enumerate(content_types))
        filters.append(f"content_type::text IN ({placeholders})")
        for i, ct in enumerate(content_types):
            params[f"ct_{i}"] = ct

    where_sql = ("WHERE " + " AND ".join(filters)) if filters else ""

    sql = text(f"""
        SELECT
            chunk_text,
            content_type,
            chunk_index,
            (embedding <=> CAST(:embedding AS vector))  AS distance,
            COALESCE(
                transcript_id::text,
                note_id::text,
                post_id::text
            )                                           AS source_id,
            class_id::text
        FROM content_embeddings
        {where_sql}
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :top_k
    """)

    try:
        rows = db.execute(sql, params).fetchall()
    except Exception as exc:
        log.error("similarity_search SQL error: %s", exc)
        return []

    results = []
    for row in rows:
        distance = float(row[3]) if row[3] is not None else 1.0
        if distance > max_distance:
            continue
        results.append(
            SearchResult(
                chunk_text=row[0],
                content_type=row[1],
                chunk_index=row[2],
                distance=distance,
                source_id=row[4],
                class_id=row[5],
            )
        )

    return results


# ── Index management ──────────────────────────────────────────────────────────

def build_hnsw_index(db: Session, *, replace: bool = False) -> bool:
    """
    Create (or recreate) an HNSW index on content_embeddings.embedding for
    fast approximate nearest-neighbour search.

    This should be called once after a bulk embedding run, not per-request.
    HNSW is non-blocking in PostgreSQL 16 (CONCURRENTLY option handled below).

    Args:
        db:       SQLAlchemy session (autocommit mode must be OFF — we COMMIT here).
        replace:  If True, DROP the existing index first and rebuild from scratch.

    Returns:
        True on success, False if the operation failed (non-fatal).
    """
    index_name = "idx_content_embeddings_hnsw"

    try:
        if replace:
            db.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
            db.commit()
            log.info("Dropped existing HNSW index %s", index_name)

        # CREATE INDEX … CONCURRENTLY cannot run inside a transaction block,
        # so we use the non-concurrent form here (acceptable for background jobs).
        db.execute(text(f"""
            CREATE INDEX IF NOT EXISTS {index_name}
            ON content_embeddings
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """))
        db.commit()
        log.info("HNSW index %s created/verified", index_name)
        return True

    except Exception as exc:
        log.error("build_hnsw_index failed: %s", exc)
        db.rollback()
        return False


# ── Upsert / delete helpers ───────────────────────────────────────────────────

def upsert_embedding(
    db: Session,
    *,
    chunk_text: str,
    content_type: str,
    class_id: Optional[UUID] = None,
    subject: Optional[str] = None,
    transcript_id: Optional[UUID] = None,
    note_id: Optional[UUID] = None,
    post_id: Optional[UUID] = None,
    chunk_index: Optional[int] = None,
) -> bool:
    """
    Generate and insert a single embedding row.
    Does NOT deduplicate — callers should check existence first if needed.

    Returns True if row was inserted successfully.
    """
    from app.models.ai import ContentEmbedding

    embedding = _embed(chunk_text)   # None in dev mode

    try:
        row = ContentEmbedding(
            class_id=class_id,
            subject=subject,
            transcript_id=transcript_id,
            note_id=note_id,
            post_id=post_id,
            content_type=content_type,
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            token_count=len(chunk_text.split()),
            embedding=embedding,
        )
        db.add(row)
        db.flush()
        return True
    except Exception as exc:
        log.error("upsert_embedding failed: %s", exc)
        db.rollback()
        return False


def delete_embeddings(
    db: Session,
    *,
    class_id: Optional[UUID] = None,
    transcript_id: Optional[UUID] = None,
    note_id: Optional[UUID] = None,
    post_id: Optional[UUID] = None,
) -> int:
    """
    Delete content_embeddings matching the given filter.
    At least one filter must be provided.

    Returns the number of rows deleted.
    """
    from app.models.ai import ContentEmbedding

    if all(v is None for v in (class_id, transcript_id, note_id, post_id)):
        raise ValueError("delete_embeddings: at least one filter is required")

    query = db.query(ContentEmbedding)
    if class_id:
        query = query.filter(ContentEmbedding.class_id == class_id)
    if transcript_id:
        query = query.filter(ContentEmbedding.transcript_id == transcript_id)
    if note_id:
        query = query.filter(ContentEmbedding.note_id == note_id)
    if post_id:
        query = query.filter(ContentEmbedding.post_id == post_id)

    count = query.count()
    query.delete(synchronize_session=False)
    db.flush()
    return count


# ── Diagnostics ───────────────────────────────────────────────────────────────

def embedding_stats(db: Session, class_id: UUID) -> EmbeddingStats:
    """
    Return coverage statistics for a class's embeddings.
    Useful for the teacher dashboard to show embedding status.
    """
    sql = text("""
        SELECT
            COUNT(*)                                                    AS total,
            COUNT(*) FILTER (WHERE content_type = 'transcript_chunk')  AS transcript,
            COUNT(*) FILTER (WHERE content_type = 'note_section')      AS note,
            COUNT(*) FILTER (WHERE content_type = 'community_post')    AS community,
            COUNT(*) FILTER (WHERE embedding IS NOT NULL)              AS with_emb,
            COUNT(*) FILTER (WHERE embedding IS NULL)                  AS without_emb
        FROM content_embeddings
        WHERE class_id = :class_id
    """)

    try:
        row = db.execute(sql, {"class_id": str(class_id)}).fetchone()
        return EmbeddingStats(
            class_id=str(class_id),
            total_chunks=row[0] or 0,
            transcript_chunks=row[1] or 0,
            note_chunks=row[2] or 0,
            community_chunks=row[3] or 0,
            chunks_with_embedding=row[4] or 0,
            chunks_without_embedding=row[5] or 0,
        )
    except Exception as exc:
        log.error("embedding_stats failed: %s", exc)
        return EmbeddingStats(
            class_id=str(class_id),
            total_chunks=0,
            transcript_chunks=0,
            note_chunks=0,
            community_chunks=0,
            chunks_with_embedding=0,
            chunks_without_embedding=0,
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _embed(text: str) -> Optional[List[float]]:
    """
    Call Vertex AI text-embedding-004.
    Returns None in dev mode (GCP not configured) — all callers must handle None.
    """
    try:
        from app.services.vertex_ai import generate_embedding
        return generate_embedding(text)
    except Exception as exc:
        log.debug("_embed failed (likely dev mode): %s", exc)
        return None


def _format_vector(values: List[float]) -> str:
    """
    Format a Python float list as a pgvector literal: '[0.1, 0.2, ...]'
    pgvector accepts this string cast: CAST(:val AS vector)
    """
    return "[" + ",".join(str(v) for v in values) + "]"