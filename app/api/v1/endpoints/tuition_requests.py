# app/api/v1/endpoints/tuition_requests.py
# Tuition request endpoints
#
# Student flow:
#   POST   /tuition-requests/           → raise request to a teacher
#   GET    /tuition-requests/me         → see own sent requests
#   DELETE /tuition-requests/{id}       → cancel a pending request
#
# Teacher flow:
#   GET    /tuition-requests/incoming   → see pending requests from students
#   PATCH  /tuition-requests/{id}/accept  → accept → auto-creates enrollment
#   PATCH  /tuition-requests/{id}/decline → decline with optional reason
#
# Teacher searches for students:
#   GET    /tuition-requests/students/search → filter by grade/subject/city

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login
from app.db.session import get_db
from app.models.student import Enrollment, StudentProfile
from app.models.subscription import Subscription
from app.models.teacher import TeacherProfile
from app.models.tuition_request import TuitionRequest
from app.models.user import User
from app.schemas.tuition_request import (
    MessageResponse,
    StudentSearchItem,
    TuitionRequestCreate,
    TuitionRequestDecline,
    TuitionRequestListItem,
    TuitionRequestResponse,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_subscribed(user_id: UUID, db: Session) -> bool:
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _get_student_profile(user_id: UUID, db: Session) -> StudentProfile:
    profile = db.query(StudentProfile).filter(
        StudentProfile.user_id == user_id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Student profile not found.")
    return profile


def _get_teacher_profile(user_id: UUID, db: Session) -> TeacherProfile:
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == user_id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    return profile


def _build_response(req: TuitionRequest, db: Session) -> TuitionRequestResponse:
    """Build full response object from a TuitionRequest row."""
    # Student
    student_profile = db.query(StudentProfile).filter(
        StudentProfile.id == req.student_id
    ).first()
    student_user = db.query(User).filter(
        User.id == student_profile.user_id
    ).first()

    # Teacher
    teacher_profile = db.query(TeacherProfile).filter(
        TeacherProfile.id == req.teacher_id
    ).first()
    teacher_user = db.query(User).filter(
        User.id == teacher_profile.user_id
    ).first()

    return TuitionRequestResponse(
        id=req.id,
        status=req.status,
        student_id=req.student_id,
        student_name=student_user.full_name,
        student_avatar_url=student_user.avatar_url,
        student_grade=student_profile.grade,
        teacher_id=req.teacher_id,
        teacher_name=teacher_user.full_name,
        teacher_avatar_url=teacher_user.avatar_url,
        teacher_is_verified=teacher_profile.is_verified,
        subject=req.subject,
        message=req.message,
        decline_reason=req.decline_reason,
        enrollment_id=req.enrollment_id,
        created_at=req.created_at,
        responded_at=req.responded_at,
    )


# ── Student Endpoints ─────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=TuitionRequestResponse,
    status_code=201,
    summary="Student raises a tuition request to a teacher",
)
def create_tuition_request(
    payload: TuitionRequestCreate,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Student sends a tuition request to a teacher for a specific subject.
    Requires an active subscription.
    Cannot send duplicate pending requests to the same teacher for the same subject.
    """
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    if not _is_subscribed(current_user.id, db):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "An active subscription is required to request tuition.",
                "cta": "View plans",
                "redirect": "/plans.html",
            },
        )

    student_profile = _get_student_profile(current_user.id, db)

    # Check teacher exists and is active
    teacher_profile = db.query(TeacherProfile).filter(
        TeacherProfile.id == payload.teacher_id
    ).first()
    if not teacher_profile:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    teacher_user = db.query(User).filter(
        and_(User.id == teacher_profile.user_id, User.is_active == True)
    ).first()
    if not teacher_user:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    # Block if already enrolled with this teacher for this subject
    already_enrolled = db.query(Enrollment).filter(
        and_(
            Enrollment.student_id == student_profile.id,
            Enrollment.teacher_id == payload.teacher_id,
            Enrollment.subject == payload.subject,
            Enrollment.is_active == True,
        )
    ).first()
    if already_enrolled:
        raise HTTPException(
            status_code=409,
            detail=f"Already enrolled with this teacher for {payload.subject}.",
        )

    # Block duplicate pending requests
    existing = db.query(TuitionRequest).filter(
        and_(
            TuitionRequest.student_id == student_profile.id,
            TuitionRequest.teacher_id == payload.teacher_id,
            TuitionRequest.subject == payload.subject,
            TuitionRequest.status == "pending",
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A pending request to this teacher for this subject already exists.",
        )

    req = TuitionRequest(
        student_id=student_profile.id,
        teacher_id=payload.teacher_id,
        subject=payload.subject,
        message=payload.message,
        grade=student_profile.grade,
        status="pending",
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    return _build_response(req, db)


@router.get(
    "/me",
    response_model=List[TuitionRequestListItem],
    summary="Student sees own sent requests",
)
def list_my_requests(
    status: Optional[str] = Query(None, description="Filter: pending|accepted|declined|cancelled"),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """List all tuition requests sent by the current student."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    student_profile = _get_student_profile(current_user.id, db)

    query = db.query(TuitionRequest, TeacherProfile, User).join(
        TeacherProfile, TeacherProfile.id == TuitionRequest.teacher_id
    ).join(
        User, User.id == TeacherProfile.user_id
    ).filter(
        TuitionRequest.student_id == student_profile.id
    )

    if status:
        query = query.filter(TuitionRequest.status == status)

    results = query.order_by(TuitionRequest.created_at.desc()).all()

    return [
        TuitionRequestListItem(
            id=req.id,
            status=req.status,
            subject=req.subject,
            message=req.message,
            decline_reason=req.decline_reason,
            enrollment_id=req.enrollment_id,
            created_at=req.created_at,
            responded_at=req.responded_at,
            counterparty_id=teacher_profile.id,
            counterparty_name=teacher_user.full_name,
            counterparty_avatar_url=teacher_user.avatar_url,
            counterparty_is_verified=teacher_profile.is_verified,
        )
        for req, teacher_profile, teacher_user in results
    ]


@router.delete(
    "/{request_id}",
    response_model=MessageResponse,
    summary="Student cancels a pending request",
)
def cancel_request(
    request_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Cancel a pending tuition request. Only the student who sent it can cancel."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    student_profile = _get_student_profile(current_user.id, db)

    req = db.query(TuitionRequest).filter(
        and_(
            TuitionRequest.id == request_id,
            TuitionRequest.student_id == student_profile.id,
        )
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")

    if req.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a request with status '{req.status}'.",
        )

    req.status = "cancelled"
    req.updated_at = datetime.now(timezone.utc)
    db.commit()

    return MessageResponse(message="Tuition request cancelled.")


# ── Teacher Endpoints ─────────────────────────────────────────────────────────

@router.get(
    "/incoming",
    response_model=List[TuitionRequestListItem],
    summary="Teacher sees incoming tuition requests",
)
def list_incoming_requests(
    status: Optional[str] = Query("pending", description="Filter: pending|accepted|declined|all"),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    List tuition requests sent to the current teacher.
    Defaults to showing only pending requests.
    Pass status=all to see everything.
    """
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher access only.")

    teacher_profile = _get_teacher_profile(current_user.id, db)

    query = db.query(TuitionRequest, StudentProfile, User).join(
        StudentProfile, StudentProfile.id == TuitionRequest.student_id
    ).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        TuitionRequest.teacher_id == teacher_profile.id
    )

    if status and status != "all":
        query = query.filter(TuitionRequest.status == status)

    results = query.order_by(TuitionRequest.created_at.desc()).all()

    return [
        TuitionRequestListItem(
            id=req.id,
            status=req.status,
            subject=req.subject,
            message=req.message,
            decline_reason=req.decline_reason,
            enrollment_id=req.enrollment_id,
            created_at=req.created_at,
            responded_at=req.responded_at,
            counterparty_id=student_profile.id,
            counterparty_name=student_user.full_name,
            counterparty_avatar_url=student_user.avatar_url,
            counterparty_grade=student_profile.grade,
        )
        for req, student_profile, student_user in results
    ]


@router.patch(
    "/{request_id}/accept",
    response_model=TuitionRequestResponse,
    summary="Teacher accepts a tuition request",
)
def accept_request(
    request_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Teacher accepts a tuition request.
    Auto-creates an Enrollment record and links it to the request.
    Student's teacher count is updated.
    """
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher access only.")

    teacher_profile = _get_teacher_profile(current_user.id, db)

    req = db.query(TuitionRequest).filter(
        and_(
            TuitionRequest.id == request_id,
            TuitionRequest.teacher_id == teacher_profile.id,
        )
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")

    if req.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot accept a request with status '{req.status}'.",
        )

    # Check not already enrolled (safety guard)
    existing_enrollment = db.query(Enrollment).filter(
        and_(
            Enrollment.student_id == req.student_id,
            Enrollment.teacher_id == teacher_profile.id,
            Enrollment.subject == req.subject,
            Enrollment.is_active == True,
        )
    ).first()
    if existing_enrollment:
        raise HTTPException(
            status_code=409,
            detail="Student is already enrolled for this subject.",
        )

    # Create enrollment
    enrollment = Enrollment(
        student_id=req.student_id,
        teacher_id=teacher_profile.id,
        subject=req.subject,
        is_active=True,
    )
    db.add(enrollment)
    db.flush()  # Get enrollment.id before commit

    # Update request
    req.status = "accepted"
    req.enrollment_id = enrollment.id
    req.responded_at = datetime.now(timezone.utc)
    req.updated_at = datetime.now(timezone.utc)

    # Update teacher's student count
    teacher_profile.total_students = db.query(Enrollment).filter(
        and_(
            Enrollment.teacher_id == teacher_profile.id,
            Enrollment.is_active == True,
        )
    ).count() + 1

    db.commit()
    db.refresh(req)

    return _build_response(req, db)


@router.patch(
    "/{request_id}/decline",
    response_model=TuitionRequestResponse,
    summary="Teacher declines a tuition request",
)
def decline_request(
    request_id: UUID,
    payload: TuitionRequestDecline,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Teacher declines a request with an optional reason."""
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher access only.")

    teacher_profile = _get_teacher_profile(current_user.id, db)

    req = db.query(TuitionRequest).filter(
        and_(
            TuitionRequest.id == request_id,
            TuitionRequest.teacher_id == teacher_profile.id,
        )
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")

    if req.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot decline a request with status '{req.status}'.",
        )

    req.status = "declined"
    req.decline_reason = payload.decline_reason
    req.responded_at = datetime.now(timezone.utc)
    req.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)

    return _build_response(req, db)


# ── Teacher: Search Students ──────────────────────────────────────────────────

@router.get(
    "/students/search",
    response_model=List[StudentSearchItem],
    summary="Teacher searches for available students",
)
def search_students(
    grade: Optional[int] = Query(None, description="Filter by school grade (5-10)"),
    city: Optional[str] = Query(None, description="Filter by city"),
    state: Optional[str] = Query(None, description="Filter by state"),
    subject: Optional[str] = Query(None, description="Filter students not yet enrolled for this subject"),
    subscribed_only: bool = Query(True, description="Only show subscribed students"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Teacher searches for students to invite or review.
    Filters: grade, city, state, subject (not already enrolled for it).
    Shows subscription status and whether already enrolled with this teacher.
    """
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher access only.")

    teacher_profile = _get_teacher_profile(current_user.id, db)

    query = db.query(StudentProfile, User).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        User.is_active == True
    )

    if grade:
        query = query.filter(StudentProfile.grade == grade)
    if city:
        query = query.filter(StudentProfile.city.ilike(f"%{city}%"))
    if state:
        query = query.filter(StudentProfile.state.ilike(f"%{state}%"))

    results = query.order_by(
        StudentProfile.performance_score.desc()
    ).offset(skip).limit(limit).all()

    # Build active subscription set for these users
    user_ids = [user.id for _, user in results]
    subscribed_user_ids = set(
        row[0] for row in db.query(Subscription.user_id).filter(
            and_(
                Subscription.user_id.in_(user_ids),
                Subscription.status == "active",
            )
        ).all()
    )

    # Build already-enrolled student_ids for this teacher
    enrolled_student_ids = set(
        row[0] for row in db.query(Enrollment.student_id).filter(
            and_(
                Enrollment.teacher_id == teacher_profile.id,
                Enrollment.is_active == True,
            )
        ).all()
    )

    items = []
    for student_profile, student_user in results:
        is_sub = student_user.id in subscribed_user_ids
        if subscribed_only and not is_sub:
            continue

        # If subject filter: skip students already enrolled for that subject with this teacher
        if subject:
            already = db.query(Enrollment).filter(
                and_(
                    Enrollment.student_id == student_profile.id,
                    Enrollment.teacher_id == teacher_profile.id,
                    Enrollment.subject == subject,
                    Enrollment.is_active == True,
                )
            ).first()
            if already:
                continue

        items.append(StudentSearchItem(
            student_id=student_profile.id,
            user_id=student_user.id,
            full_name=student_user.full_name,
            avatar_url=student_user.avatar_url,
            grade=student_profile.grade,
            city=student_profile.city,
            state=student_profile.state,
            performance_score=student_profile.performance_score,
            badges=student_profile.badges,
            streak_days=student_profile.streak_days,
            is_subscribed=is_sub,
            already_enrolled=student_profile.id in enrolled_student_ids,
        ))

    return items