# app/api/v1/endpoints/channels.py
# Content embedding trigger endpoint
#
# Teacher calls POST /channels/embed after approving notes.
# This makes the class content searchable by Diya's RAG pipeline.
# Also exposes GET /channels/embed/{class_id} to check embedding status.

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_teacher
from app.db.session import get_db
from app.jobs.embed_content import embed_class_content
from app.models.ai import ContentEmbedding
from app.models.class_ import Class
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.schemas.embedding import EmbedRequest, EmbedStatusResponse, MessageResponse

router = APIRouter()


def _run_embed(class_id: UUID, db: Session):
    """Background task wrapper for embed_class_content."""
    try:
        embed_class_content(class_id, db)
    except Exception as e:
        print(f"Background embedding failed for class {class_id}: {e}")


@router.post(
    "/embed",
    response_model=EmbedStatusResponse,
    status_code=201,
    summary="Embed class content for RAG (teacher only)",
)
def trigger_embed(
    payload: EmbedRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Trigger embedding of a class's transcript and notes.
    Must be the teacher who owns the class.
    Runs in background -- use GET /embed/{class_id} to check status.

    When to call:
      - After notes are approved (notes_status = completed)
      - After manually editing transcript text
      - To refresh embeddings after content changes (use force=true)
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == payload.class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    if cls.notes_status != "completed" and cls.transcript_status != "completed":
        raise HTTPException(
            status_code=422,
            detail="Class must have at least a completed transcript or completed notes before embedding.",
        )

    # Run synchronously for small content, background for large
    background_tasks.add_task(_run_embed, payload.class_id, db)

    return EmbedStatusResponse(
        class_id=payload.class_id,
        transcript_chunks=0,
        note_chunks=0,
        total_chunks=0,
        status="pending",
        message="Embedding started in background. Check status with GET /channels/embed/{class_id}.",
    )


@router.get(
    "/embed/{class_id}",
    response_model=EmbedStatusResponse,
    summary="Check embedding status for a class",
)
def get_embed_status(
    class_id: UUID,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Check how many chunks have been embedded for a class."""
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    transcript_chunks = db.query(ContentEmbedding).filter(
        and_(
            ContentEmbedding.class_id == class_id,
            ContentEmbedding.transcript_id != None,
        )
    ).count()

    note_chunks = db.query(ContentEmbedding).filter(
        and_(
            ContentEmbedding.class_id == class_id,
            ContentEmbedding.note_id != None,
        )
    ).count()

    total = transcript_chunks + note_chunks
    status = "completed" if total > 0 else "pending"
    message = (
        f"{total} chunks embedded ({transcript_chunks} transcript, {note_chunks} notes)."
        if total > 0 else "No embeddings yet."
    )

    return EmbedStatusResponse(
        class_id=class_id,
        transcript_chunks=transcript_chunks,
        note_chunks=note_chunks,
        total_chunks=total,
        status=status,
        message=message,
    )