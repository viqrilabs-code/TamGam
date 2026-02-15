# app/api/v1/endpoints/transcripts.py
# Transcript pipeline endpoints
#
# Flow:
#   1. Teacher links Google Drive file to a class (POST)
#   2. System downloads .docx and extracts text (synchronous for MVP)
#   3. Transcript stored with status=completed
#   4. Students with subscription can read the raw text
#   5. AI Notes generation triggered after transcript is ready (Component 11)

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_teacher
from app.db.session import get_db
from app.models.class_ import Class
from app.models.subscription import Subscription
from app.models.teacher import TeacherProfile
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.transcript import (
    MessageResponse,
    TranscriptLinkRequest,
    TranscriptResponse,
    TranscriptUpdateRequest,
)
from app.services import google_drive

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_subscribed(user_id, db):
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _build_response(transcript: Transcript, viewer: User, db) -> TranscriptResponse:
    show_text = False
    text_gated = False
    if viewer:
        if viewer.role in ("teacher", "admin"):
            show_text = True
        elif viewer.role == "student":
            if _is_subscribed(viewer.id, db):
                show_text = True
            elif transcript.raw_text:
                text_gated = True

    return TranscriptResponse(
        id=transcript.id,
        class_id=transcript.class_id,
        drive_file_id=transcript.drive_file_id,
        drive_file_name=transcript.drive_file_name,
        status=transcript.status,
        raw_text=transcript.raw_text if show_text else None,
        word_count=transcript.word_count,
        raw_text_gated=text_gated,
        created_at=transcript.created_at,
        updated_at=transcript.updated_at,
    )


def _process_transcript(transcript_id: UUID, db: Session):
    """
    Background task: download Drive file and extract text.
    Updates transcript status to completed or failed.
    """
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        return

    transcript.status = "processing"
    db.commit()

    try:
        raw_text = google_drive.download_docx_as_text(transcript.drive_file_id)
        if raw_text:
            transcript.raw_text = raw_text
            transcript.word_count = len(raw_text.split())
            transcript.status = "completed"
            # Flag the class transcript as completed
            cls = db.query(Class).filter(Class.id == transcript.class_id).first()
            if cls:
                cls.transcript_status = "completed"
        else:
            transcript.status = "failed"
            cls = db.query(Class).filter(Class.id == transcript.class_id).first()
            if cls:
                cls.transcript_status = "failed"
    except Exception as e:
        transcript.status = "failed"
        print(f"Transcript processing failed: {e}")

    transcript.updated_at = datetime.now(timezone.utc)
    db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/{class_id}",
    response_model=TranscriptResponse,
    status_code=201,
    summary="Link Google Drive transcript to a class (teacher only)",
)
def link_transcript(
    class_id: UUID,
    payload: TranscriptLinkRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher links a Google Drive .docx file to a class.
    Text extraction runs as a background task.
    In development (no Drive credentials), mock text is used immediately.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    if not tp:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    # Check no transcript exists already
    existing = db.query(Transcript).filter(Transcript.class_id == class_id).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A transcript already exists for this class. Use PATCH to update it.",
        )

    # Get file metadata from Drive (or mock)
    metadata = google_drive.get_file_metadata(payload.drive_file_id)
    file_name = payload.drive_file_name or (metadata.get("name") if metadata else None)

    transcript = Transcript(
        class_id=class_id,
        drive_file_id=payload.drive_file_id,
        drive_file_name=file_name,
        status="pending",
    )
    db.add(transcript)
    cls.transcript_status = "pending"
    db.commit()
    db.refresh(transcript)

    # Process in background
    background_tasks.add_task(_process_transcript, transcript.id, db)

    return _build_response(transcript, current_user, db)


@router.get(
    "/{class_id}",
    response_model=TranscriptResponse,
    summary="Get transcript for a class",
)
def get_transcript(
    class_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Get transcript for a class.
    Raw text is subscription-gated for students.
    Teachers always see their own transcripts.
    """
    transcript = db.query(Transcript).filter(Transcript.class_id == class_id).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="No transcript found for this class.")
    return _build_response(transcript, current_user, db)


@router.patch(
    "/{class_id}",
    response_model=TranscriptResponse,
    summary="Update transcript text (teacher only)",
)
def update_transcript(
    class_id: UUID,
    payload: TranscriptUpdateRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher can manually correct the extracted transcript text.
    Updates word count automatically.
    Resets notes_status on the class to trigger re-generation.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    transcript = db.query(Transcript).filter(Transcript.class_id == class_id).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="No transcript found for this class.")

    transcript.raw_text = payload.raw_text
    transcript.word_count = len(payload.raw_text.split())
    transcript.status = "completed"
    transcript.updated_at = datetime.now(timezone.utc)

    # Reset notes so they get regenerated with corrected text
    cls.transcript_status = "completed"
    cls.notes_status = None

    db.commit()
    db.refresh(transcript)
    return _build_response(transcript, current_user, db)


@router.delete(
    "/{class_id}",
    response_model=MessageResponse,
    summary="Delete transcript (teacher only)",
)
def delete_transcript(
    class_id: UUID,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    transcript = db.query(Transcript).filter(Transcript.class_id == class_id).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="No transcript found for this class.")

    db.delete(transcript)
    cls.transcript_status = None
    cls.notes_status = None
    db.commit()
    return MessageResponse(message="Transcript deleted.")