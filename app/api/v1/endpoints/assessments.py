# app/api/v1/endpoints/assessments.py
# Adaptive assessment endpoints
#
# Model reality:
#   - StudentAssessment owns both questions AND student answers (per student per class)
#   - Understanding level is integer 1-5 (not string)
#   - Questions have band: below | at_level | above
#   - Level recomputes every 3 classes as moving average

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_teacher
from app.db.session import get_db
from app.models.assessment import StudentAssessment, StudentUnderstandingProfile
from app.models.class_ import Class
from app.models.note import Note
from app.models.student import StudentProfile
from app.models.subscription import Subscription
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.schemas.assessment import (
    AnswerResult,
    AssessmentHistoryItem,
    AssessmentResponse,
    AssessmentSubmitRequest,
    AssessmentSubmitResponse,
    AssessmentTeacherResponse,
    MCQOption,
    Question,
    QuestionForStudent,
)

router = APIRouter()

# Level labels for display
LEVEL_LABELS = {1: "beginner", 2: "developing", 3: "proficient", 4: "advanced", 5: "expert"}
LABEL_TO_INT = {v: k for k, v in LEVEL_LABELS.items()}

# Points per band
BAND_POINTS = {"below": 2, "at_level": 3, "above": 4}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_subscribed(user_id, db):
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _get_or_create_understanding(student_id, subject, db) -> StudentUnderstandingProfile:
    profile = db.query(StudentUnderstandingProfile).filter(
        and_(
            StudentUnderstandingProfile.student_id == student_id,
            StudentUnderstandingProfile.subject == subject,
        )
    ).first()
    if not profile:
        profile = StudentUnderstandingProfile(
            student_id=student_id,
            subject=subject,
            current_level=3,
            classes_since_last_recompute=0,
            total_classes_assessed=0,
        )
        db.add(profile)
        db.flush()
    return profile


def _recompute_level(profile: StudentUnderstandingProfile, percentage: float, class_id, db):
    """
    Update understanding level.
    Recomputes every 3 classes as moving average of recent scores.
    Returns (new_level_label, changed, old_level_label).
    """
    old_level = profile.current_level
    old_label = LEVEL_LABELS.get(old_level, "proficient")

    # Update recent_scores list
    recent = list(profile.recent_scores or [])
    recent.append({"class_id": str(class_id), "score": percentage})
    if len(recent) > 10:
        recent = recent[-10:]
    profile.recent_scores = recent

    profile.classes_since_last_recompute += 1
    profile.total_classes_assessed += 1
    profile.last_recomputed_at = datetime.now(timezone.utc)

    # Recompute every 3 classes
    if profile.classes_since_last_recompute >= 3:
        avg = sum(r["score"] for r in recent[-3:]) / 3
        profile.previous_level = old_level
        if avg >= 80 and old_level < 5:
            profile.current_level = old_level + 1
        elif avg < 40 and old_level > 1:
            profile.current_level = old_level - 1
        profile.classes_since_last_recompute = 0
    else:
        # Single-class nudge
        profile.previous_level = old_level
        if percentage >= 90 and old_level < 5:
            profile.current_level = old_level + 1
        elif percentage < 30 and old_level > 1:
            profile.current_level = old_level - 1

    new_label = LEVEL_LABELS.get(profile.current_level, "proficient")
    changed = profile.current_level != old_level
    return new_label, changed, old_label


def _mock_questions(subject: str, level: int) -> List[dict]:
    return [
        {
            "id": 1, "type": "mcq", "band": "below",
            "text": "What is a variable in algebra?",
            "options": ["A fixed number", "A letter representing an unknown", "An operator", "An equation"],
            "answer": "A letter representing an unknown",
            "explanation": "A variable like x or y represents an unknown number.",
            "hint": "Think about what changes in a formula.",
        },
        {
            "id": 2, "type": "mcq", "band": "at_level",
            "text": "What is the value of 2x + 3 when x = 4?",
            "options": ["9", "10", "11", "14"],
            "answer": "11",
            "explanation": "2(4)+3 = 8+3 = 11.",
            "hint": "Substitute x with 4.",
        },
        {
            "id": 3, "type": "mcq", "band": "at_level",
            "text": "Simplify: 3x + 2x + 5",
            "options": ["5x + 5", "6x + 5", "5x", "10x"],
            "answer": "5x + 5",
            "explanation": "3x + 2x = 5x, result is 5x + 5.",
            "hint": "Combine like terms first.",
        },
        {
            "id": 4, "type": "short_answer", "band": "above",
            "text": "If 3y - 7 = 8, what is the value of y?",
            "options": None,
            "answer": "5",
            "explanation": "3y = 15, y = 5.",
            "hint": "Isolate y by moving constants to the other side.",
        },
        {
            "id": 5, "type": "mcq", "band": "below",
            "text": "Which is an algebraic expression?",
            "options": ["42", "x + 3", "=", "None"],
            "answer": "x + 3",
            "explanation": "Algebraic expressions contain variables.",
            "hint": "Look for letters combined with numbers.",
        },
    ]


def _questions_for_student(questions: list) -> List[QuestionForStudent]:
    result = []
    for q in questions:
        opts = None
        if q.get("options"):
            opts = [MCQOption(key=str(i+1), text=o) if isinstance(o, str)
                    else MCQOption(**o) for i, o in enumerate(q["options"])]
        result.append(QuestionForStudent(
            id=q["id"], type=q["type"],
            level=LEVEL_LABELS.get(3, "proficient"),
            question=q.get("text", q.get("question", "")),
            options=opts,
        ))
    return result


def _questions_with_answers(questions: list) -> List[Question]:
    result = []
    for q in questions:
        opts = None
        if q.get("options"):
            opts = [MCQOption(key=str(i+1), text=o) if isinstance(o, str)
                    else MCQOption(**o) for i, o in enumerate(q["options"])]
        result.append(Question(
            id=q["id"], type=q["type"],
            level=q.get("band", "at_level"),
            question=q.get("text", q.get("question", "")),
            options=opts,
            correct_answer=q.get("answer"),
            explanation=q.get("explanation"),
        ))
    return result


def _run_generation(sa_id: UUID, note_content: dict, subject: str, level: int, db: Session):
    sa = db.query(StudentAssessment).filter(StudentAssessment.id == sa_id).first()
    if not sa:
        return
    sa.status = "in_progress"
    db.commit()
    try:
        questions = _mock_questions(subject, level)
        sa.questions = questions
        sa.total_questions = len(questions)
        sa.max_score = sum(BAND_POINTS.get(q.get("band", "at_level"), 3) for q in questions)
        sa.status = "pending"  # Ready for student
        sa.level_at_generation = level
    except Exception as e:
        sa.status = "pending"
        print(f"Assessment generation error: {e}")
    sa.updated_at = datetime.now(timezone.utc) if hasattr(sa, 'updated_at') else None
    db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/{class_id}/generate",
    response_model=AssessmentResponse,
    status_code=201,
    summary="Generate assessment for a class (teacher only)",
)
def generate_assessment(
    class_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher triggers assessment generation for a class.
    Creates one StudentAssessment template per class.
    Each student gets the same questions (personalised scoring via bands).
    Requires completed notes.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    note = db.query(Note).filter(
        and_(Note.class_id == class_id, Note.status == "completed")
    ).first()
    if not note:
        raise HTTPException(
            status_code=422,
            detail="No completed notes found. Generate and approve notes first.",
        )

    # Check if template assessment already exists
    existing = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == None)
    ).first()
    if existing and existing.status in ("pending", "in_progress"):
        raise HTTPException(status_code=409, detail=f"Assessment already exists (status: {existing.status}).")

    # Create template (student_id=None means it's the class template)
    sa = StudentAssessment(
        class_id=class_id,
        student_id=None,
        status="in_progress",
        time_limit_seconds=600,
    )
    db.add(sa)
    cls.assessment_generated = True
    db.commit()
    db.refresh(sa)

    background_tasks.add_task(_run_generation, sa.id, note.content or {}, cls.subject, 3, db)

    return AssessmentResponse(
        id=sa.id, class_id=class_id, status="in_progress",
        questions=None, total_questions=None, is_gated=False,
        created_at=sa.created_at, updated_at=sa.created_at,
    )


@router.get(
    "/{class_id}",
    summary="Get assessment for a class",
)
def get_assessment(
    class_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Get class assessment questions.
    Students see questions without answers (subscription required).
    Teachers see full questions with correct answers.
    """
    sa = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == None)
    ).first()
    if not sa:
        raise HTTPException(status_code=404, detail="No assessment found for this class.")

    questions = sa.questions or []

    if current_user.role in ("teacher", "admin"):
        return AssessmentTeacherResponse(
            id=sa.id, class_id=class_id, status=sa.status,
            questions=_questions_for_student(questions),
            questions_with_answers=_questions_with_answers(questions),
            total_questions=len(questions), is_gated=False,
            created_at=sa.created_at, updated_at=sa.created_at,
        )

    if not _is_subscribed(current_user.id, db):
        return AssessmentResponse(
            id=sa.id, class_id=class_id, status=sa.status,
            questions=None, total_questions=len(questions), is_gated=True,
            created_at=sa.created_at, updated_at=sa.created_at,
        )

    return AssessmentResponse(
        id=sa.id, class_id=class_id, status=sa.status,
        questions=_questions_for_student(questions),
        total_questions=len(questions), is_gated=False,
        created_at=sa.created_at, updated_at=sa.created_at,
    )


@router.post(
    "/{class_id}/submit",
    response_model=AssessmentSubmitResponse,
    summary="Submit assessment answers",
)
def submit_assessment(
    class_id: UUID,
    payload: AssessmentSubmitRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Student submits answers. Scores automatically.
    Updates understanding level. One attempt only.
    """
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    if not _is_subscribed(current_user.id, db):
        raise HTTPException(status_code=403, detail={"message": "Active subscription required.", "redirect": "/pricing"})

    sp = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    # Get template
    template = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == None)
    ).first()
    if not template or template.status != "pending":
        raise HTTPException(status_code=404, detail="No ready assessment found for this class.")

    # Check not already submitted
    existing = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == sp.id)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="You have already submitted this assessment.")

    cls = db.query(Class).filter(Class.id == class_id).first()
    subject = cls.subject if cls else "General"
    questions = {q["id"]: q for q in (template.questions or [])}
    answer_map = {a.question_id: a.answer for a in payload.answers}

    results = []
    raw_score = 0.0
    max_score = template.max_score or 1
    bands = {"below": 0, "at_level": 0, "above": 0}

    for qid, q in questions.items():
        student_answer = answer_map.get(qid, "")
        correct = q.get("answer", "")
        is_correct = student_answer.strip().lower() == correct.strip().lower()
        band = q.get("band", "at_level")
        if is_correct:
            raw_score += BAND_POINTS.get(band, 3)
            bands[band] = bands.get(band, 0) + 1

        opts = None
        if q.get("options"):
            opts_list = q["options"]
            opts = [MCQOption(key=str(i+1), text=o) if isinstance(o, str) else MCQOption(**o)
                    for i, o in enumerate(opts_list)]

        results.append(AnswerResult(
            question_id=qid,
            question=q.get("text", q.get("question", "")),
            your_answer=student_answer,
            correct_answer=correct,
            is_correct=is_correct,
            explanation=q.get("explanation"),
        ))

    percentage = (raw_score / max_score * 100) if max_score > 0 else 0.0
    correct_count = sum(1 for r in results if r.is_correct)

    understanding = _get_or_create_understanding(sp.id, subject, db)
    new_level_label, level_changed, old_label = _recompute_level(understanding, percentage, class_id, db)

    # Create student's own record
    student_sa = StudentAssessment(
        class_id=class_id,
        student_id=sp.id,
        questions=template.questions,
        student_answers=answer_map,
        total_questions=len(questions),
        score=raw_score,
        max_score=max_score,
        percentage=round(percentage, 1),
        below_correct=bands.get("below", 0),
        at_level_correct=bands.get("at_level", 0),
        above_correct=bands.get("above", 0),
        status="evaluated",
        level_at_generation=understanding.current_level,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(student_sa)
    db.commit()

    return AssessmentSubmitResponse(
        assessment_id=student_sa.id,
        class_id=class_id,
        score=round(percentage, 1),
        correct_count=correct_count,
        total_questions=len(questions),
        results=results,
        understanding_level=new_level_label,
        level_changed=level_changed,
        previous_level=old_label if level_changed else None,
    )


@router.get(
    "/me/history",
    response_model=List[AssessmentHistoryItem],
    summary="Get own assessment history",
)
def get_assessment_history(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Student's assessment history across all classes."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    sp = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    results = db.query(StudentAssessment, Class).join(
        Class, Class.id == StudentAssessment.class_id
    ).filter(
        and_(StudentAssessment.student_id == sp.id, StudentAssessment.status == "evaluated")
    ).order_by(StudentAssessment.submitted_at.desc()).all()

    return [
        AssessmentHistoryItem(
            id=sa.id,
            class_id=cls.id,
            class_title=cls.title,
            subject=cls.subject,
            score=sa.percentage or 0.0,
            correct_count=int(sa.below_correct + sa.at_level_correct + sa.above_correct),
            total_questions=sa.total_questions or 0,
            understanding_level=LEVEL_LABELS.get(sa.level_at_generation or 3, "proficient"),
            submitted_at=sa.submitted_at or sa.created_at,
        )
        for sa, cls in results
    ]