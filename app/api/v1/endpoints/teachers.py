# app/api/v1/endpoints/teachers.py
# Teacher profile, verification, earnings, and top performers endpoints

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_teacher, resolve_user_marks
from app.db.session import get_db
from app.models.class_ import Class
from app.models.notification import Notification
from app.models.student import Batch, BatchMember, StudentProfile
from app.models.teacher_rating import TeacherRating
from app.models.teacher import (
    TeacherProfile,
    TeacherStudentVerificationRequest,
    TeacherVerification,
    TopPerformer,
    VerificationDocument,
)
from app.models.payout import TeacherPayout
from app.models.user import User
from app.services.gemini_key_manager import generate_with_fallback
from app.schemas.teacher import (
    EarningsResponse,
    MessageResponse,
    StudentVerificationRequestItem,
    TeacherListItem,
    TeacherBatchListItem,
    TeacherProfilePrivate,
    TeacherProfilePublic,
    TeacherPortfolioHighlights,
    TeacherProfileUpdate,
    TeacherPayoutItem,
    TopPerformerItem,
    TopPerformersResponse,
    VerificationDocumentResponse,
    VerificationRequestCreate,
    VerificationStudentCandidate,
    VerificationStatusResponse,
)
from app.schemas.teacher_rating import TeacherRatingPublicItem, TeacherRatingSummaryResponse

router = APIRouter()
logger = logging.getLogger("tamgam.teachers")
# Promotional flat platform fee.
TEACHER_PLATFORM_MONTHLY_FEE_PAISE = 9900


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

REQUIRED_STUDENT_VERIFICATIONS = 3


def _get_teacher_profile_or_404(current_user: User, db: Session) -> TeacherProfile:
    profile = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    return profile


def _verification_requests_for_teacher(profile_id: UUID, db: Session) -> List[TeacherStudentVerificationRequest]:
    return db.query(TeacherStudentVerificationRequest).filter(
        TeacherStudentVerificationRequest.teacher_id == profile_id
    ).order_by(TeacherStudentVerificationRequest.requested_at.desc()).all()


def _verification_counts(profile_id: UUID, db: Session) -> tuple[int, int]:
    verified_count = db.query(TeacherStudentVerificationRequest).filter(
        and_(
            TeacherStudentVerificationRequest.teacher_id == profile_id,
            TeacherStudentVerificationRequest.status == "verified",
        )
    ).count()
    pending_count = db.query(TeacherStudentVerificationRequest).filter(
        and_(
            TeacherStudentVerificationRequest.teacher_id == profile_id,
            TeacherStudentVerificationRequest.status == "pending",
        )
    ).count()
    return verified_count, pending_count


def _verification_status_from_counts(profile: TeacherProfile, verified_count: int, pending_count: int) -> str:
    if profile.is_verified or verified_count >= REQUIRED_STUDENT_VERIFICATIONS:
        return "approved"
    if pending_count > 0:
        return "pending"
    return "unverified"


def _build_student_verification_items(
    requests: List[TeacherStudentVerificationRequest], db: Session
) -> List[StudentVerificationRequestItem]:
    if not requests:
        return []
    student_ids = list({r.student_id for r in requests})
    students = db.query(StudentProfile, User).join(
        User, User.id == StudentProfile.user_id
    ).filter(StudentProfile.id.in_(student_ids)).all()
    student_map = {
        sp.id: (sp, user) for sp, user in students
    }
    items: List[StudentVerificationRequestItem] = []
    for req in requests:
        sp, user = student_map.get(req.student_id, (None, None))
        items.append(
            StudentVerificationRequestItem(
                id=req.id,
                student_id=req.student_id,
                student_name=user.full_name if user else "Student",
                student_grade=sp.grade if sp else None,
                status=req.status,
                requested_at=req.requested_at,
                responded_at=req.responded_at,
            )
        )
    return items


def _commission_rate(total_revenue_paise: int) -> float:
    """
    Promotional flat commission on total tracked revenue.
    """
    return 5.0


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_json_object(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _dedupe_keep_order(items: List[str], limit: int = 8) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        value = (item or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _build_portfolio_highlights(profile: TeacherProfile, user: User) -> TeacherPortfolioHighlights:
    education_entries = profile.education or []
    experience_entries = profile.past_job_experiences or []

    education_achievements: List[str] = []
    best_education_score: Optional[float] = None
    best_education_line: Optional[str] = None

    for entry in education_entries:
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("level") or "education").strip()
        for ach in entry.get("achievements") or []:
            if isinstance(ach, str) and ach.strip():
                education_achievements.append(ach.strip())
        score_percent = _safe_float(entry.get("score_percent"))
        marks = _safe_float(entry.get("marks_obtained"))
        total = _safe_float(entry.get("total_marks"))
        if score_percent is None and marks is not None and total and total > 0:
            score_percent = (marks / total) * 100.0
        if score_percent is not None and (best_education_score is None or score_percent > best_education_score):
            best_education_score = score_percent
            best_education_line = f"{level.upper()} score: {score_percent:.1f}%"

    experience_achievements: List[str] = []
    for entry in experience_entries:
        if not isinstance(entry, dict):
            continue
        role = (entry.get("role_title") or "").strip()
        org = (entry.get("organization") or "").strip()
        if role and org:
            experience_achievements.append(f"{role} at {org}")
        for ach in entry.get("achievements") or []:
            if isinstance(ach, str) and ach.strip():
                experience_achievements.append(ach.strip())

    education_achievements = _dedupe_keep_order(education_achievements, limit=10)
    experience_achievements = _dedupe_keep_order(experience_achievements, limit=10)

    candidate_marketable = _dedupe_keep_order(
        education_achievements + experience_achievements + ([best_education_line] if best_education_line else []),
        limit=12,
    )
    marketable_achievements = candidate_marketable[:5]

    if marketable_achievements:
        most_promising_aspect = marketable_achievements[0]
    elif best_education_line:
        most_promising_aspect = best_education_line
    elif profile.experience_years:
        most_promising_aspect = f"{profile.experience_years} years of teaching experience"
    else:
        most_promising_aspect = None

    if candidate_marketable:
        try:
            prompt = f"""
You are Diya helping build a teacher portfolio for parents and students.
Select only the strongest, marketable achievements from the provided list.
Return strict JSON with keys:
- most_promising_aspect: string
- marketable_achievements: string[] (max 5)

Teacher: {user.full_name}
Subjects: {", ".join(profile.subjects or []) or "Not specified"}
Experience years: {profile.experience_years or "Not specified"}
Candidate achievements: {json.dumps(candidate_marketable, ensure_ascii=True)}
"""
            raw = generate_with_fallback(prompt, model_name="gemini-2.0-flash")
            parsed = _extract_json_object(raw)
            if isinstance(parsed, dict):
                ai_marketable = _dedupe_keep_order(
                    [str(x) for x in (parsed.get("marketable_achievements") or []) if x],
                    limit=5,
                )
                ai_promising = (parsed.get("most_promising_aspect") or "").strip()
                if ai_marketable:
                    marketable_achievements = ai_marketable
                if ai_promising:
                    most_promising_aspect = ai_promising
                elif marketable_achievements:
                    most_promising_aspect = marketable_achievements[0]
        except Exception as exc:
            logger.info("Diya portfolio filtering unavailable, using deterministic fallback: %s", exc)

    return TeacherPortfolioHighlights(
        most_promising_aspect=most_promising_aspect,
        marketable_achievements=marketable_achievements,
        education_achievements=education_achievements,
        experience_achievements=experience_achievements,
    )


def _build_public_profile(profile: TeacherProfile, user: User, db: Session) -> TeacherProfilePublic:
    marks = resolve_user_marks(user, db)
    portfolio_highlights = _build_portfolio_highlights(profile, user)
    return TeacherProfilePublic(
        id=profile.id,
        user_id=profile.user_id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        bio=profile.bio,
        subjects=profile.subjects,
        qualifications=profile.qualifications,
        experience_years=profile.experience_years,
        school_or_institution=profile.school_or_institution,
        school_name=profile.school_name,
        preferred_language=profile.preferred_language,
        teaching_style=profile.teaching_style,
        focus_grades=profile.focus_grades,
        focus_boards=profile.focus_boards,
        class_note_tone=profile.class_note_tone,
        class_note_preferences=profile.class_note_preferences,
        education=profile.education or [],
        past_job_experiences=profile.past_job_experiences or [],
        portfolio_highlights=portfolio_highlights,
        is_verified=profile.is_verified,
        total_students=profile.total_students,
        total_classes=profile.total_classes,
        average_rating=profile.average_rating,
        rating_count=int(profile.rating_count or 0),
        is_verified_teacher=marks["is_verified_teacher"],
    )


def _build_private_profile(profile: TeacherProfile, user: User, db: Session) -> TeacherProfilePrivate:
    marks = resolve_user_marks(user, db)
    portfolio_highlights = _build_portfolio_highlights(profile, user)
    # Mask bank account number -- show only last 4 digits
    masked_account = None
    if profile.bank_account_number:
        num = profile.bank_account_number
        masked_account = f"{'*' * (len(num) - 4)}{num[-4:]}" if len(num) > 4 else "****"

    return TeacherProfilePrivate(
        id=profile.id,
        user_id=profile.user_id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        bio=profile.bio,
        subjects=profile.subjects,
        qualifications=profile.qualifications,
        experience_years=profile.experience_years,
        school_or_institution=profile.school_or_institution,
        school_name=profile.school_name,
        preferred_language=profile.preferred_language,
        teaching_style=profile.teaching_style,
        focus_grades=profile.focus_grades,
        focus_boards=profile.focus_boards,
        class_note_tone=profile.class_note_tone,
        class_note_preferences=profile.class_note_preferences,
        education=profile.education or [],
        past_job_experiences=profile.past_job_experiences or [],
        portfolio_highlights=portfolio_highlights,
        is_verified=profile.is_verified,
        total_students=profile.total_students,
        total_classes=profile.total_classes,
        average_rating=profile.average_rating,
        rating_count=int(profile.rating_count or 0),
        is_verified_teacher=marks["is_verified_teacher"],
        bank_account_name=profile.bank_account_name,
        bank_account_number=masked_account,
        bank_ifsc_code=profile.bank_ifsc_code,
        bank_upi_id=profile.bank_upi_id,
        razorpay_contact_id=profile.razorpay_contact_id,
        razorpay_fund_account_id=profile.razorpay_fund_account_id,
        total_revenue_paise=profile.total_revenue_paise,
        platform_commission_paise=profile.platform_commission_paise,
        verified_at=profile.verified_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


# â”€â”€ Public Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _get_upcoming_class_times(teacher_id: UUID, db: Session, limit: int = 3) -> List[datetime]:
    """Return upcoming scheduled/live class times for a teacher."""
    now = datetime.now(timezone.utc)
    rows = db.query(Class.scheduled_at).filter(
        and_(
            Class.teacher_id == teacher_id,
            Class.status.in_(["scheduled", "live"]),
            Class.scheduled_at >= now,
        )
    ).order_by(Class.scheduled_at.asc()).limit(limit).all()
    return [row[0] for row in rows]


def _get_public_batches_for_teacher(teacher_id: UUID, db: Session) -> List[TeacherBatchListItem]:
    batches = db.query(Batch).filter(
        and_(
            Batch.teacher_id == teacher_id,
            Batch.is_active == True,
            Batch.student_selection_enabled == True,
        )
    ).order_by(Batch.created_at.desc()).all()

    if not batches:
        return []

    batch_ids = [b.id for b in batches]
    member_counts = db.query(BatchMember.batch_id, BatchMember.id).filter(
        BatchMember.batch_id.in_(batch_ids)
    ).all()
    counts = {}
    for batch_id, _ in member_counts:
        counts[batch_id] = counts.get(batch_id, 0) + 1

    items: List[TeacherBatchListItem] = []
    for b in batches:
        count = counts.get(b.id, 0)
        if b.max_students is not None and count >= b.max_students:
            continue
        seats_left = None if b.max_students is None else max(0, int(b.max_students) - count)
        items.append(
            TeacherBatchListItem(
                id=b.id,
                name=b.name,
                subject=b.subject,
                description=b.description,
                grade_level=b.grade_level,
                class_timing=b.default_timing,
                class_days=b.class_days or [],
                max_students=b.max_students,
                member_count=count,
                seats_left=seats_left,
                fee_paise=int(b.fee_paise or 0),
                fee_rupees=(b.fee_paise or 0) / 100,
            )
        )
    return items


def _build_teacher_rating_summary(
    teacher_id: UUID,
    db: Session,
    *,
    limit: int = 10,
) -> TeacherRatingSummaryResponse:
    profile = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    avg_rating, rating_count = db.query(
        func.avg(TeacherRating.rating),
        func.count(TeacherRating.id),
    ).filter(
        TeacherRating.teacher_id == teacher_id
    ).one()

    avg_value = float(avg_rating) if avg_rating is not None else None
    count_value = int(rating_count or 0)

    rows = db.query(TeacherRating, User).join(
        StudentProfile, StudentProfile.id == TeacherRating.student_id
    ).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        TeacherRating.teacher_id == teacher_id
    ).order_by(
        TeacherRating.updated_at.desc()
    ).limit(limit).all()

    items = [
        TeacherRatingPublicItem(
            rating=rating.rating,
            comment=(rating.comment or "").strip() or None,
            student_name=user.full_name,
            created_at=rating.created_at,
            updated_at=rating.updated_at,
        )
        for rating, user in rows
    ]

    return TeacherRatingSummaryResponse(
        teacher_id=teacher_id,
        average_rating=avg_value,
        rating_count=count_value,
        ratings=items,
    )


@router.get(
    "/",
    response_model=List[TeacherListItem],
    summary="List verified teachers (public)",
)
def list_teachers(
    subject: Optional[str] = Query(None, description="Filter by subject"),
    q: Optional[str] = Query(None, description="General search by teacher name/email/school"),
    name: Optional[str] = Query(None, description="Search by teacher name"),
    email: Optional[str] = Query(None, description="Search by teacher email"),
    school: Optional[str] = Query(None, description="Search by school/institution"),
    verified_only: bool = Query(True, description="Return only verified teachers"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Public teacher discovery endpoint.
    Returns only verified teachers by default.
    Optionally filter by subject and/or search query.
    """
    query = db.query(TeacherProfile, User).join(
        User, User.id == TeacherProfile.user_id
    ).filter(
        User.is_active == True,
    )

    if verified_only:
        query = query.filter(TeacherProfile.is_verified == True)

    if subject:
        query = query.filter(TeacherProfile.subjects.any(subject))
    if name:
        query = query.filter(User.full_name.ilike(f"%{name.strip()}%"))
    if email:
        query = query.filter(User.email.ilike(f"%{email.strip()}%"))
    if school:
        school_like = f"%{school.strip()}%"
        query = query.filter(
            or_(
                TeacherProfile.school_or_institution.ilike(school_like),
                TeacherProfile.school_name.ilike(school_like),
            )
        )
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.full_name.ilike(like),
                User.email.ilike(like),
                TeacherProfile.school_or_institution.ilike(like),
                TeacherProfile.school_name.ilike(like),
            )
        )

    results = query.order_by(TeacherProfile.total_students.desc()).offset(skip).limit(limit).all()

    return [
        TeacherListItem(
            id=profile.id,
            user_id=profile.user_id,
            full_name=user.full_name,
            school_or_institution=profile.school_or_institution,
            school_name=profile.school_name,
            avatar_url=user.avatar_url,
            subjects=profile.subjects,
            experience_years=profile.experience_years,
            is_verified=profile.is_verified,
            total_students=profile.total_students,
            average_rating=profile.average_rating,
            rating_count=int(profile.rating_count or 0),
            upcoming_class_times=_get_upcoming_class_times(profile.id, db),
            available_batches=_get_public_batches_for_teacher(profile.id, db),
        )
        for profile, user in results
    ]


@router.get(
    "/{teacher_id}/public",
    response_model=TeacherProfilePublic,
    summary="Get teacher public profile",
)
def get_teacher_public(teacher_id: UUID, db: Session = Depends(get_db)):
    """
    Public teacher profile -- visible to all users including anonymous.
    Does not include bank details or earnings.
    """
    profile = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher not found.")
    user = db.query(User).filter(User.id == profile.user_id).first()
    return _build_public_profile(profile, user, db)


@router.get(
    "/{teacher_id}/ratings",
    response_model=TeacherRatingSummaryResponse,
    summary="Get teacher ratings summary (public)",
)
def get_teacher_ratings(
    teacher_id: UUID,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return _build_teacher_rating_summary(teacher_id=teacher_id, db=db, limit=limit)


@router.get(
    "/{teacher_id}/top-performers",
    response_model=TopPerformersResponse,
    summary="Get top performing students for a teacher (public)",
)
def get_top_performers(teacher_id: UUID, db: Session = Depends(get_db)):
    """
    Top performers cached by the recompute_rankings Cloud Run Job.
    Public endpoint -- shown on teacher's profile page.
    Only exposes public student info (name, avatar, score).
    """
    profile = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    performers = db.query(TopPerformer, User).join(
        User,
        User.id == db.query(
            __import__('app.models.student', fromlist=['StudentProfile']).StudentProfile.user_id
        ).filter(
            __import__('app.models.student', fromlist=['StudentProfile']).StudentProfile.id == TopPerformer.student_id
        ).scalar_subquery()
    ).filter(
        TopPerformer.teacher_id == teacher_id
    ).order_by(TopPerformer.rank).all()

    items = [
        TopPerformerItem(
            rank=tp.rank,
            student_id=tp.student_id,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            performance_score=tp.performance_score,
            computed_at=tp.computed_at,
        )
        for tp, user in performers
    ]

    computed_at = performers[0][0].computed_at if performers else None
    return TopPerformersResponse(teacher_id=teacher_id, performers=items, computed_at=computed_at)


# â”€â”€ Authenticated Teacher Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/me",
    response_model=TeacherProfilePrivate,
    summary="Get own full teacher profile",
)
def get_my_profile(
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Full private profile -- only accessible by the teacher themselves."""
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    return _build_private_profile(profile, current_user, db)


@router.patch(
    "/me",
    response_model=TeacherProfilePrivate,
    summary="Update own teacher profile",
)
def update_my_profile(
    payload: TeacherProfileUpdate,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Update teacher profile fields.
    Only non-None fields in the payload are updated.
    is_verified and earnings cannot be set here -- admin-only.
    """
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(profile, field, value)

    profile.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(profile)
    return _build_private_profile(profile, current_user, db)


@router.get(
    "/me/verification",
    response_model=VerificationStatusResponse,
    summary="Get own verification status",
)
def get_verification_status(
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Returns teacher verification progress based on student verification requests."""
    profile = _get_teacher_profile_or_404(current_user, db)
    requests = _verification_requests_for_teacher(profile.id, db)
    verified_count, pending_count = _verification_counts(profile.id, db)
    status_value = _verification_status_from_counts(profile, verified_count, pending_count)

    latest_request_at = requests[0].requested_at if requests else None
    can_request_more = (not profile.is_verified) and (
        verified_count < REQUIRED_STUDENT_VERIFICATIONS and
        pending_count < (REQUIRED_STUDENT_VERIFICATIONS - verified_count)
    )

    return VerificationStatusResponse(
        has_submitted=bool(requests),
        status=status_value,
        submitted_at=latest_request_at,
        reviewed_at=profile.verified_at if profile.is_verified else None,
        rejection_reason=None,
        documents=[],
        verification_mode="student",
        required_verifications=REQUIRED_STUDENT_VERIFICATIONS,
        verified_count=verified_count,
        pending_count=pending_count,
        can_request_more=can_request_more,
        requests=_build_student_verification_items(requests, db),
    )


@router.post(
    "/me/verification/requests",
    response_model=VerificationStatusResponse,
    status_code=201,
    summary="Request student verification for T badge",
)
def request_student_verification(
    payload: VerificationRequestCreate,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """Teacher requests one or more students (max 3 total) to verify them."""
    profile = _get_teacher_profile_or_404(current_user, db)
    verified_count, pending_count = _verification_counts(profile.id, db)

    if profile.is_verified or verified_count >= REQUIRED_STUDENT_VERIFICATIONS:
        raise HTTPException(status_code=409, detail="Teacher is already verified.")

    available_slots = REQUIRED_STUDENT_VERIFICATIONS - verified_count - pending_count
    if available_slots <= 0:
        raise HTTPException(
            status_code=409,
            detail="You already have enough pending requests. Wait for student responses.",
        )
    if len(payload.student_ids) > available_slots:
        raise HTTPException(
            status_code=409,
            detail=f"You can request only {available_slots} more student(s) right now.",
        )

    unique_ids = list(dict.fromkeys(payload.student_ids))
    if len(unique_ids) != len(payload.student_ids):
        raise HTTPException(status_code=422, detail="Duplicate students are not allowed.")

    existing_requests = db.query(TeacherStudentVerificationRequest).filter(
        and_(
            TeacherStudentVerificationRequest.teacher_id == profile.id,
            TeacherStudentVerificationRequest.student_id.in_(unique_ids),
        )
    ).all()
    if existing_requests:
        raise HTTPException(
            status_code=409,
            detail="One or more selected students already received a verification request. Choose different students.",
        )

    students = db.query(StudentProfile, User).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        and_(
            StudentProfile.id.in_(unique_ids),
            User.role == "student",
            User.is_active == True,
        )
    ).all()
    student_map = {sp.id: (sp, u) for sp, u in students}

    for student_id in unique_ids:
        if student_id not in student_map:
            raise HTTPException(status_code=404, detail="One or more students were not found.")

    for student_id in unique_ids:
        sp, student_user = student_map[student_id]
        req = TeacherStudentVerificationRequest(
            teacher_id=profile.id,
            student_id=sp.id,
            status="pending",
        )
        db.add(req)
        db.flush()

        db.add(Notification(
            user_id=student_user.id,
            notification_type="announcement",
            title="Teacher verification request",
            body=f"{current_user.full_name} asked you to verify them for the T badge.",
            action_url="/dashboard.html#notifications-panel",
            extra_data={
                "kind": "teacher_verification_request",
                "verification_request_id": str(req.id),
                "teacher_id": str(profile.id),
                "teacher_name": current_user.full_name,
                "student_id": str(sp.id),
                "status": "pending",
            },
            is_read=False,
        ))

    db.commit()
    return get_verification_status(current_user=current_user, db=db)


@router.get(
    "/me/verification/students/search",
    response_model=List[VerificationStudentCandidate],
    summary="Search students by name or email for verification requests",
)
def search_students_for_verification(
    q: Optional[str] = Query(None, description="Name or email search"),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    profile = _get_teacher_profile_or_404(current_user, db)
    requested_student_ids = {
        row[0]
        for row in db.query(TeacherStudentVerificationRequest.student_id).filter(
            TeacherStudentVerificationRequest.teacher_id == profile.id
        ).all()
    }

    query = db.query(StudentProfile, User).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        and_(
            User.role == "student",
            User.is_active == True,
        )
    )
    if requested_student_ids:
        query = query.filter(~StudentProfile.id.in_(requested_student_ids))

    text = (q or "").strip()
    if text:
        like = f"%{text}%"
        query = query.filter(
            or_(
                User.full_name.ilike(like),
                User.email.ilike(like),
            )
        )

    rows = query.order_by(User.full_name.asc()).limit(limit).all()
    return [
        VerificationStudentCandidate(
            student_id=sp.id,
            full_name=user.full_name,
            email=user.email,
            grade=sp.grade,
        )
        for sp, user in rows
    ]


@router.post(
    "/me/verification",
    response_model=VerificationStatusResponse,
    status_code=201,
    summary="Deprecated: document verification is disabled",
)
def submit_verification(
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    raise HTTPException(
        status_code=410,
        detail="Document submission is disabled. Use student verification requests instead.",
    )


@router.get(
    "/me/earnings",
    response_model=EarningsResponse,
    summary="Get own earnings and commission breakdown",
)
def get_my_earnings(
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher earnings breakdown with current commission tier.
    Commission is flat 5% for the current promotional offer.
    """
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    net = profile.total_revenue_paise - profile.platform_commission_paise
    rate = _commission_rate(profile.total_revenue_paise)

    return EarningsResponse(
        total_revenue_paise=profile.total_revenue_paise,
        platform_commission_paise=profile.platform_commission_paise,
        net_earnings_paise=net,
        current_commission_rate_percent=rate,
        total_revenue_rupees=profile.total_revenue_paise / 100,
        net_earnings_rupees=net / 100,
        this_month_paise=net,
        last_month_paise=0,
        total_paise=net,
        platform_monthly_fee_paise=TEACHER_PLATFORM_MONTHLY_FEE_PAISE,
    )


@router.get(
    "/me/payouts",
    response_model=List[TeacherPayoutItem],
    summary="Get own payout history",
)
def get_my_payout_history(
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    profile = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    payouts = db.query(TeacherPayout).filter(
        TeacherPayout.teacher_id == profile.id
    ).order_by(TeacherPayout.created_at.desc()).all()

    return [
        TeacherPayoutItem(
            id=row.id,
            period_start=row.period_start,
            period_end=row.period_end,
            net_amount_paise=row.net_amount_paise,
            net_amount_rupees=row.net_amount_paise / 100,
            status=row.status,
            razorpay_payout_id=row.razorpay_payout_id,
            razorpay_status=row.razorpay_status,
            failure_reason=row.failure_reason,
            created_at=row.created_at,
            processed_at=row.processed_at,
        )
        for row in payouts
    ]

