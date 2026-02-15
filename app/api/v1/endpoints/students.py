# app/api/v1/endpoints/students.py
# Student profile, enrollment, and batch endpoints

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_subscription, resolve_user_marks
from app.db.session import get_db
from app.models.student import BatchMember, Enrollment, StudentProfile, Batch
from app.models.teacher import TeacherProfile
from app.models.subscription import Subscription
from app.models.user import User
from app.schemas.student import (
    BatchResponse,
    EnrollmentResponse,
    EnrollRequest,
    MessageResponse,
    StudentProfilePrivate,
    StudentProfilePublic,
    StudentProfileUpdate,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_student_profile_or_404(user_id, db: Session) -> StudentProfile:
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Student profile not found.")
    return profile


def _is_subscribed(user_id, db: Session) -> bool:
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _build_private_profile(profile: StudentProfile, user: User, db: Session) -> StudentProfilePrivate:
    return StudentProfilePrivate(
        id=profile.id,
        user_id=profile.user_id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        grade=profile.grade,
        performance_score=profile.performance_score,
        badges=profile.badges,
        streak_days=profile.streak_days,
        date_of_birth=profile.date_of_birth,
        parent_name=profile.parent_name,
        parent_phone=profile.parent_phone,
        parent_email=None,
        address_city=profile.city,
        address_state=profile.state,
        address_pincode=profile.pincode,
        is_subscribed=_is_subscribed(user.id, db),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


# ── Profile Endpoints ─────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=StudentProfilePrivate,
    summary="Get own student profile",
)
def get_my_profile(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Full private profile -- only accessible by the student themselves."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    profile = _get_student_profile_or_404(current_user.id, db)
    return _build_private_profile(profile, current_user, db)


@router.patch(
    "/me",
    response_model=StudentProfilePrivate,
    summary="Update own student profile",
)
def update_my_profile(
    payload: StudentProfileUpdate,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Update student profile fields. Only non-None fields are updated."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    profile = _get_student_profile_or_404(current_user.id, db)

    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(profile, field, value)

    profile.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(profile)
    return _build_private_profile(profile, current_user, db)


@router.get(
    "/{student_id}/public",
    response_model=StudentProfilePublic,
    summary="Get student public profile",
)
def get_student_public(student_id: UUID, db: Session = Depends(get_db)):
    """
    Public student profile -- only shows name, avatar, grade, badges, streak.
    No personal or contact info exposed.
    """
    profile = db.query(StudentProfile).filter(StudentProfile.id == student_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Student not found.")
    user = db.query(User).filter(User.id == profile.user_id).first()
    return StudentProfilePublic(
        id=profile.id,
        user_id=profile.user_id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        grade=profile.grade,
        performance_score=profile.performance_score,
        badges=profile.badges,
        streak_days=profile.streak_days,
    )


# ── Enrollment Endpoints ──────────────────────────────────────────────────────

@router.get(
    "/me/enrollments",
    response_model=List[EnrollmentResponse],
    summary="List own enrollments",
)
def list_my_enrollments(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """List all active enrollments for the current student."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    profile = _get_student_profile_or_404(current_user.id, db)

    enrollments = db.query(Enrollment, TeacherProfile, User).join(
        TeacherProfile, TeacherProfile.id == Enrollment.teacher_id
    ).join(
        User, User.id == TeacherProfile.user_id
    ).filter(
        and_(
            Enrollment.student_id == profile.id,
            Enrollment.is_active == True,
        )
    ).all()

    return [
        EnrollmentResponse(
            id=enrollment.id,
            teacher_id=teacher_profile.id,
            teacher_name=teacher_user.full_name,
            teacher_avatar_url=teacher_user.avatar_url,
            teacher_is_verified=teacher_profile.is_verified,
            subject=enrollment.subject,
            is_active=enrollment.is_active,
            enrolled_at=enrollment.enrolled_at,
        )
        for enrollment, teacher_profile, teacher_user in enrollments
    ]


@router.post(
    "/me/enroll",
    response_model=EnrollmentResponse,
    status_code=201,
    summary="Enroll with a teacher",
)
def enroll_with_teacher(
    payload: EnrollRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Enroll with a teacher for a specific subject.
    Requires an active subscription.
    Cannot enroll with the same teacher for the same subject twice.
    """
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    # Check subscription
    if not _is_subscribed(current_user.id, db):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "An active subscription is required to enroll with a teacher.",
                "cta": "View plans",
                "redirect": "/pricing",
            },
        )

    profile = _get_student_profile_or_404(current_user.id, db)

    # Check teacher exists
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

    # Check not already enrolled
    existing = db.query(Enrollment).filter(
        and_(
            Enrollment.student_id == profile.id,
            Enrollment.teacher_id == payload.teacher_id,
            Enrollment.subject == payload.subject,
            Enrollment.is_active == True,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Already enrolled with this teacher for {payload.subject}.",
        )

    enrollment = Enrollment(
        student_id=profile.id,
        teacher_id=payload.teacher_id,
        subject=payload.subject,
        is_active=True,
    )
    db.add(enrollment)

    # Update teacher's total_students count
    teacher_profile.total_students = db.query(Enrollment).filter(
        and_(
            Enrollment.teacher_id == payload.teacher_id,
            Enrollment.is_active == True,
        )
    ).count() + 1

    db.commit()
    db.refresh(enrollment)

    return EnrollmentResponse(
        id=enrollment.id,
        teacher_id=teacher_profile.id,
        teacher_name=teacher_user.full_name,
        teacher_avatar_url=teacher_user.avatar_url,
        teacher_is_verified=teacher_profile.is_verified,
        subject=enrollment.subject,
        is_active=enrollment.is_active,
        enrolled_at=enrollment.enrolled_at,
    )


@router.delete(
    "/me/enroll/{enrollment_id}",
    response_model=MessageResponse,
    summary="Unenroll from a teacher",
)
def unenroll(
    enrollment_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Deactivate an enrollment. Does not delete the record."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    profile = _get_student_profile_or_404(current_user.id, db)

    enrollment = db.query(Enrollment).filter(
        and_(
            Enrollment.id == enrollment_id,
            Enrollment.student_id == profile.id,
        )
    ).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found.")

    enrollment.is_active = False

    # Update teacher student count
    teacher_profile = db.query(TeacherProfile).filter(
        TeacherProfile.id == enrollment.teacher_id
    ).first()
    if teacher_profile:
        active_count = db.query(Enrollment).filter(
            and_(
                Enrollment.teacher_id == enrollment.teacher_id,
                Enrollment.is_active == True,
            )
        ).count()
        teacher_profile.total_students = max(0, active_count - 1)

    db.commit()
    return MessageResponse(message="Unenrolled successfully.")


# ── Batch Endpoints ───────────────────────────────────────────────────────────

@router.get(
    "/me/batches",
    response_model=List[BatchResponse],
    summary="List batches the student belongs to",
)
def list_my_batches(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """List all batches the current student has been added to."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    profile = _get_student_profile_or_404(current_user.id, db)

    results = db.query(BatchMember, Batch, TeacherProfile, User).join(
        Batch, Batch.id == BatchMember.batch_id
    ).join(
        TeacherProfile, TeacherProfile.id == Batch.teacher_id
    ).join(
        User, User.id == TeacherProfile.user_id
    ).filter(
        BatchMember.student_id == profile.id
    ).all()

    return [
        BatchResponse(
            id=batch.id,
            name=batch.name,
            description=batch.description,
            teacher_id=teacher_profile.id,
            teacher_name=teacher_user.full_name,
            subject=batch.subject,
            is_active=batch.is_active,
            joined_at=member.joined_at,
        )
        for member, batch, teacher_profile, teacher_user in results
    ]