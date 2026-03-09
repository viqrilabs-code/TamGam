# app/api/v1/endpoints/students.py
# Student profile, enrollment, and batch endpoints

from datetime import datetime, timedelta, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import (
    get_effective_active_subscription,
    require_login,
)
from app.db.session import get_db
from app.models.student import BatchMember, Enrollment, StudentProfile, Batch
from app.models.assessment import StudentUnderstandingProfile
from app.models.teacher_rating import TeacherRating
from app.models.teacher import TeacherProfile
from app.models.notification import Notification
from app.models.user import User
from app.schemas.teacher_rating import (
    TeacherRatingEligibilityResponse,
    TeacherRatingResponse,
    TeacherRatingUpsertRequest,
)
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
MIN_DAYS_BEFORE_RATING = 30


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _get_student_profile_or_404(user_id, db: Session) -> StudentProfile:
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Student profile not found.")
    return profile


def _is_subscribed(user_id, db: Session) -> bool:
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.role == "student":
        return True
    return get_effective_active_subscription(user_id, db) is not None


def _sync_due_unenrollments(
    db: Session,
    *,
    student_profile_id: UUID | None = None,
    teacher_id: UUID | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    query = db.query(Enrollment).filter(
        and_(
            Enrollment.is_active == True,
            Enrollment.pending_unenroll_at.isnot(None),
            Enrollment.pending_unenroll_at <= now,
        )
    )
    if student_profile_id:
        query = query.filter(Enrollment.student_id == student_profile_id)
    if teacher_id:
        query = query.filter(Enrollment.teacher_id == teacher_id)

    due = query.all()
    if not due:
        return
    for enrollment in due:
        enrollment.is_active = False
        enrollment.unenrolled_at = enrollment.pending_unenroll_at or now
        enrollment.pending_unenroll_at = None
    db.flush()


def _build_private_profile(profile: StudentProfile, user: User, db: Session) -> StudentProfilePrivate:
    understanding = db.query(StudentUnderstandingProfile).filter(
        StudentUnderstandingProfile.student_id == profile.id
    ).order_by(StudentUnderstandingProfile.updated_at.desc()).first()
    return StudentProfilePrivate(
        id=profile.id,
        user_id=profile.user_id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        grade=profile.grade,
        school_name=profile.school_name,
        preferred_language=profile.preferred_language,
        learning_style=profile.learning_style,
        target_exam=profile.target_exam,
        strengths=profile.strengths,
        improvement_areas=profile.improvement_areas,
        learning_goals=profile.learning_goals,
        weekly_study_hours=profile.weekly_study_hours,
        understanding_level=(int(understanding.current_level) if understanding and understanding.current_level is not None else None),
        performance_score=profile.performance_score,
        badges=profile.badges,
        streak_days=profile.streak_days,
        date_of_birth=profile.date_of_birth,
        parent_name=profile.parent_name,
        parent_phone=profile.parent_phone,
        address_city=profile.city,
        address_state=profile.state,
        address_pincode=profile.pincode,
        is_subscribed=_is_subscribed(user.id, db),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _to_rating_response(row: TeacherRating) -> TeacherRatingResponse:
    return TeacherRatingResponse(
        id=row.id,
        teacher_id=row.teacher_id,
        student_id=row.student_id,
        rating=row.rating,
        comment=(row.comment or "").strip() or None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _refresh_teacher_rating_snapshot(teacher_id: UUID, db: Session) -> None:
    avg_value, count_value = db.query(
        func.avg(TeacherRating.rating),
        func.count(TeacherRating.id),
    ).filter(
        TeacherRating.teacher_id == teacher_id
    ).one()

    profile = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not profile:
        return
    profile.average_rating = float(avg_value) if avg_value is not None else None
    profile.rating_count = int(count_value or 0)


def _rating_eligibility(
    *,
    student_profile_id: UUID,
    teacher_id: UUID,
    db: Session,
) -> tuple[bool, bool, datetime | None, int, str | None]:
    enrollment = db.query(Enrollment).filter(
        and_(
            Enrollment.student_id == student_profile_id,
            Enrollment.teacher_id == teacher_id,
            Enrollment.is_active == True,
        )
    ).order_by(Enrollment.enrolled_at.asc()).first()

    if not enrollment:
        return False, False, None, 0, "You can rate only teachers you are actively enrolled with."

    eligible_from = enrollment.enrolled_at + timedelta(days=MIN_DAYS_BEFORE_RATING)
    now = datetime.now(timezone.utc)
    if now >= eligible_from:
        return True, True, eligible_from, 0, None

    delta = eligible_from - now
    days_remaining = int(delta.total_seconds() // 86400)
    if (delta.total_seconds() % 86400) > 0:
        days_remaining += 1
    reason = (
        f"Rating unlocks after {MIN_DAYS_BEFORE_RATING} days from enrollment. "
        f"You can rate from {eligible_from.date().isoformat()}."
    )
    return True, False, eligible_from, max(0, days_remaining), reason


# â”€â”€ Profile Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    field_map = {
        "address_city": "city",
        "address_state": "state",
        "address_pincode": "pincode",
    }
    for field, value in update_data.items():
        setattr(profile, field_map.get(field, field), value)

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
    understanding = db.query(StudentUnderstandingProfile).filter(
        StudentUnderstandingProfile.student_id == profile.id
    ).order_by(StudentUnderstandingProfile.updated_at.desc()).first()
    return StudentProfilePublic(
        id=profile.id,
        user_id=profile.user_id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        grade=profile.grade,
        school_name=profile.school_name,
        preferred_language=profile.preferred_language,
        learning_style=profile.learning_style,
        target_exam=profile.target_exam,
        strengths=profile.strengths,
        improvement_areas=profile.improvement_areas,
        learning_goals=profile.learning_goals,
        weekly_study_hours=profile.weekly_study_hours,
        understanding_level=(int(understanding.current_level) if understanding and understanding.current_level is not None else None),
        performance_score=profile.performance_score,
        badges=profile.badges,
        streak_days=profile.streak_days,
    )


# â”€â”€ Enrollment Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    _sync_due_unenrollments(db, student_profile_id=profile.id)

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
            pending_unenroll_at=enrollment.pending_unenroll_at,
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
    Students are free by default.
    Cannot enroll with the same teacher for the same subject twice.
    """
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    profile = _get_student_profile_or_404(current_user.id, db)
    _sync_due_unenrollments(db, student_profile_id=profile.id)

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
        pending_unenroll_at=enrollment.pending_unenroll_at,
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
    """Schedule unenrollment at billing period end. Does not delete the record."""
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

    min_unenroll_at = enrollment.enrolled_at + timedelta(days=30)
    if datetime.now(timezone.utc) < min_unenroll_at:
        raise HTTPException(
            status_code=409,
            detail=(
                "Minimum enrollment duration is 1 month. "
                f"You can unenroll after {min_unenroll_at.date().isoformat()}."
            ),
        )

    if enrollment.pending_unenroll_at:
        raise HTTPException(
            status_code=409,
            detail=f"Unenrollment already scheduled for {enrollment.pending_unenroll_at.date().isoformat()}.",
        )

    active_sub = get_effective_active_subscription(current_user.id, db)
    effective_at = datetime.now(timezone.utc)
    if active_sub and active_sub.current_period_end and active_sub.current_period_end > effective_at:
        effective_at = active_sub.current_period_end
    enrollment.pending_unenroll_at = effective_at

    # Update teacher student count if unenrollment is immediate.
    teacher_profile = db.query(TeacherProfile).filter(
        TeacherProfile.id == enrollment.teacher_id
    ).first()
    teacher_user = None
    if teacher_profile:
        teacher_user = db.query(User).filter(User.id == teacher_profile.user_id).first()
        if effective_at <= datetime.now(timezone.utc):
            active_count = db.query(Enrollment).filter(
                and_(
                    Enrollment.teacher_id == enrollment.teacher_id,
                    Enrollment.is_active == True,
                )
            ).count()
            teacher_profile.total_students = max(0, active_count - 1)

    # Notify teacher that unenrollment is scheduled at cycle end.
    if teacher_user:
        db.add(
            Notification(
                user_id=teacher_user.id,
                notification_type="announcement",
                title="Student unenrollment scheduled",
                body=(
                    f"{current_user.full_name} will be unenrolled from "
                    f"{enrollment.subject or 'your classes'} on {effective_at.date().isoformat()}."
                ),
                action_url="/teacher-dashboard.html",
                extra_data={
                    "kind": "unenrollment",
                    "student_user_id": str(current_user.id),
                    "teacher_id": str(enrollment.teacher_id),
                    "subject": enrollment.subject,
                    "enrollment_id": str(enrollment.id),
                    "effective_at": effective_at.isoformat(),
                },
            )
        )

    db.commit()
    return MessageResponse(
        message=(
            "Unenrollment scheduled. Access to this teacher remains until "
            f"{effective_at.date().isoformat()}."
        )
    )


@router.get(
    "/me/teacher-ratings/{teacher_id}/eligibility",
    response_model=TeacherRatingEligibilityResponse,
    summary="Check whether current student can rate a teacher",
)
def get_teacher_rating_eligibility(
    teacher_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    student_profile = _get_student_profile_or_404(current_user.id, db)
    teacher = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    is_enrolled, can_rate, eligible_from, days_remaining, reason = _rating_eligibility(
        student_profile_id=student_profile.id,
        teacher_id=teacher_id,
        db=db,
    )
    existing = db.query(TeacherRating).filter(
        and_(
            TeacherRating.teacher_id == teacher_id,
            TeacherRating.student_id == student_profile.id,
        )
    ).first()

    return TeacherRatingEligibilityResponse(
        teacher_id=teacher_id,
        is_enrolled=is_enrolled,
        can_rate=can_rate,
        eligible_from=eligible_from,
        days_remaining=days_remaining,
        reason=reason,
        existing_rating=_to_rating_response(existing) if existing else None,
    )


@router.post(
    "/me/teacher-ratings/{teacher_id}",
    response_model=TeacherRatingResponse,
    summary="Create or update rating for an enrolled teacher",
)
def upsert_teacher_rating(
    teacher_id: UUID,
    payload: TeacherRatingUpsertRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    student_profile = _get_student_profile_or_404(current_user.id, db)
    teacher = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    is_enrolled, can_rate, eligible_from, _, reason = _rating_eligibility(
        student_profile_id=student_profile.id,
        teacher_id=teacher_id,
        db=db,
    )
    if not is_enrolled:
        raise HTTPException(status_code=403, detail=reason or "Enrollment required before rating.")
    if not can_rate:
        raise HTTPException(
            status_code=409,
            detail=reason or f"You can rate from {eligible_from.date().isoformat()}.",
        )

    comment = (payload.comment or "").strip() or None
    if comment and len(comment) > 1000:
        raise HTTPException(status_code=422, detail="Comment must be 1000 characters or less.")

    row = db.query(TeacherRating).filter(
        and_(
            TeacherRating.teacher_id == teacher_id,
            TeacherRating.student_id == student_profile.id,
        )
    ).first()
    now = datetime.now(timezone.utc)

    if row:
        row.rating = payload.rating
        row.comment = comment
        row.updated_at = now
    else:
        row = TeacherRating(
            teacher_id=teacher_id,
            student_id=student_profile.id,
            rating=payload.rating,
            comment=comment,
            created_at=now,
            updated_at=now,
        )
        db.add(row)

    _refresh_teacher_rating_snapshot(teacher_id, db)
    db.commit()
    db.refresh(row)
    return _to_rating_response(row)


# Batch Endpoints

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
            fee_paise=int(batch.fee_paise or 0),
            fee_rupees=(batch.fee_paise or 0) / 100,
            is_active=batch.is_active,
            joined_at=member.joined_at,
        )
        for member, batch, teacher_profile, teacher_user in results
    ]

