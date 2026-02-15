# app/api/v1/endpoints/notes.py
# AI Notes generation and management endpoints
#
# Flow:
#   1. Teacher triggers generation after transcript is ready
#   2. Gemini generates structured notes in background
#   3. Teacher reviews (approve/reject)
#   4. Approved notes visible to subscribed students

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_teacher
from app.db.session import get_db
from app.models.class_ import Class
from app.models.note import Note
from app.models.subscription import Subscription
from app.models.teacher import TeacherProfile
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.note import (
    MessageResponse,
    NoteContent,
    NoteEditRequest,
    NoteResponse,
    NoteReviewRequest,
    QAPair,
)
from app.services import vertex_ai

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_subscribed(user_id, db):
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _parse_content(raw: dict) -> NoteContent:
    """Parse raw dict from DB/Gemini into NoteContent schema."""
    return NoteContent(
        summary=raw.get("summary", ""),
        key_points=raw.get("key_points", []),
        detailed_notes=raw.get("detailed_notes", ""),
        qa_pairs=[QAPair(**qa) for qa in raw.get("qa_pairs", [])],
    )


def _build_response(note: Note, viewer: User, db) -> NoteResponse:
    show_content = False
    content_gated = False
    if viewer:
        if viewer.role in ("teacher", "admin"):
            show_content = True
        elif viewer.role == "student":
            if _is_subscribed(viewer.id, db):
                show_content = True
            elif note.content:
                content_gated = True

    content = None
    if show_content and note.content:
        content = _parse_content(note.content)

    return NoteResponse(
        id=note.id,
        class_id=note.class_id,
        status=note.status,
        content=content,
        content_gated=content_gated,
        teacher_reviewed=note.teacher_reviewed,
        teacher_review_notes=note.teacher_review_notes,
        ai_model_used=note.ai_model_used,
        generation_prompt_tokens=note.generation_prompt_tokens,
        generation_output_tokens=note.generation_output_tokens,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _run_generation(note_id: UUID, transcript_text: str, db: Session):
    """Background task: call Gemini and store structured notes."""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        return

    note.status = "generating"
    db.commit()

    try:
        result = vertex_ai.generate_notes(transcript_text)
        if result:
            note.content = result
            note.status = "completed"
            note.ai_model_used = "gemini-2.5-flash"
            # Update class notes_status
            cls = db.query(Class).filter(Class.id == note.class_id).first()
            if cls:
                cls.notes_status = "completed"
        else:
            note.status = "failed"
            cls = db.query(Class).filter(Class.id == note.class_id).first()
            if cls:
                cls.notes_status = "failed"
    except Exception as e:
        note.status = "failed"
        print(f"Notes generation failed: {e}")

    note.updated_at = datetime.now(timezone.utc)
    db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/{class_id}/generate",
    response_model=NoteResponse,
    status_code=201,
    summary="Generate AI notes from transcript (teacher only)",
)
def generate_notes(
    class_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Trigger AI notes generation for a class.
    Requires a completed transcript.
    Runs Gemini in background -- poll GET to check status.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    transcript = db.query(Transcript).filter(
        and_(Transcript.class_id == class_id, Transcript.status == "completed")
    ).first()
    if not transcript:
        raise HTTPException(
            status_code=422,
            detail="No completed transcript found. Upload and process a transcript first.",
        )

    # Check no note already generating or completed
    existing = db.query(Note).filter(Note.class_id == class_id).first()
    if existing:
        if existing.status in ("generating", "completed"):
            raise HTTPException(
                status_code=409,
                detail=f"Notes already exist with status '{existing.status}'. Use PATCH to edit or review.",
            )
        # Failed note -- allow regeneration
        db.delete(existing)
        db.flush()

    note = Note(
        class_id=class_id,
        status="pending",
        teacher_reviewed=False,
    )
    db.add(note)
    cls.notes_status = "pending"
    db.commit()
    db.refresh(note)

    background_tasks.add_task(_run_generation, note.id, transcript.raw_text, db)

    return _build_response(note, current_user, db)


@router.get(
    "/{class_id}",
    response_model=NoteResponse,
    summary="Get notes for a class",
)
def get_notes(
    class_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Get AI-generated notes for a class.
    Content is subscription-gated for students.
    Teachers always see their own class notes.
    """
    note = db.query(Note).filter(Note.class_id == class_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="No notes found for this class.")
    return _build_response(note, current_user, db)


@router.patch(
    "/{class_id}/review",
    response_model=NoteResponse,
    summary="Teacher reviews AI notes (approve or reject)",
)
def review_notes(
    class_id: UUID,
    payload: NoteReviewRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher approves or rejects AI-generated notes.
    Rejected notes can be regenerated or manually edited.
    Students only see notes that have been approved.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    note = db.query(Note).filter(Note.class_id == class_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="No notes found for this class.")
    if note.status != "completed":
        raise HTTPException(
            status_code=422,
            detail=f"Notes are not ready for review (status: {note.status}).",
        )

    if payload.approved:
        note.status = "completed"
        note.teacher_reviewed = True
        note.teacher_review_notes = payload.review_notes
    else:
        if not payload.review_notes:
            raise HTTPException(
                status_code=422,
                detail="review_notes is required when rejecting notes.",
            )
        note.status = "rejected"
        note.teacher_reviewed = True
        note.teacher_review_notes = payload.review_notes
        cls.notes_status = "rejected"

    note.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(note)
    return _build_response(note, current_user, db)


@router.patch(
    "/{class_id}",
    response_model=NoteResponse,
    summary="Teacher edits note content directly",
)
def edit_notes(
    class_id: UUID,
    payload: NoteEditRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher manually edits note content.
    Auto-marks as reviewed and approved.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    note = db.query(Note).filter(Note.class_id == class_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="No notes found for this class.")

    note.content = payload.content.model_dump()
    note.status = "completed"
    note.teacher_reviewed = True
    note.updated_at = datetime.now(timezone.utc)
    cls.notes_status = "completed"

    db.commit()
    db.refresh(note)
    return _build_response(note, current_user, db)