# app/api/v1/endpoints/teachers.py
# Teacher profile, verification, earnings, and top performers endpoints

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_teacher, resolve_user_marks
from app.db.session import get_db
from app.models.teacher import TeacherProfile, TeacherVerification, TopPerformer, VerificationDocument
from app.models.user import User
from app.schemas.teacher import (
    EarningsResponse,
    MessageResponse,
    TeacherListItem,
    TeacherProfilePrivate,
    TeacherProfilePublic,
    TeacherProfileUpdate,
    TopPerformerItem,
    TopPerformersResponse,
    VerificationDocumentResponse,
    VerificationStatusResponse,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _commission_rate(total_revenue_paise: int) -> float:
    """
    Commission tiers (on total lifetime revenue):
      0 – 50,000 rupees    → 20%
      50,001 – 2,00,000    → 15%
      2,00,001+            → 10%
    """
    total_rupees = total_revenue_paise / 100
    if total_rupees <= 50000:
        return 20.0
    elif total_rupees <= 200000:
        return 15.0
    return 10.0


def _build_public_profile(profile: TeacherProfile, user: User, db: Session) -> TeacherProfilePublic:
    marks = resolve_user_marks(user, db)
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
        is_verified=profile.is_verified,
        total_students=profile.total_students,
        total_classes=profile.total_classes,
        average_rating=profile.average_rating,
        is_verified_teacher=marks["is_verified_teacher"],
    )


def _build_private_profile(profile: TeacherProfile, user: User, db: Session) -> TeacherProfilePrivate:
    marks = resolve_user_marks(user, db)
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
        is_verified=profile.is_verified,
        total_students=profile.total_students,
        total_classes=profile.total_classes,
        average_rating=profile.average_rating,
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


# ── Public Endpoints ──────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=List[TeacherListItem],
    summary="List verified teachers (public)",
)
def list_teachers(
    subject: Optional[str] = Query(None, description="Filter by subject"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Public teacher discovery endpoint.
    Returns only verified teachers by default.
    Optionally filter by subject.
    """
    query = db.query(TeacherProfile, User).join(
        User, User.id == TeacherProfile.user_id
    ).filter(
        and_(
            TeacherProfile.is_verified == True,
            User.is_active == True,
        )
    )

    if subject:
        query = query.filter(TeacherProfile.subjects.any(subject))

    results = query.order_by(TeacherProfile.total_students.desc()).offset(skip).limit(limit).all()

    return [
        TeacherListItem(
            id=profile.id,
            user_id=profile.user_id,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            subjects=profile.subjects,
            experience_years=profile.experience_years,
            is_verified=profile.is_verified,
            total_students=profile.total_students,
            average_rating=profile.average_rating,
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


# ── Authenticated Teacher Endpoints ──────────────────────────────────────────

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
    """
    Returns the teacher's current verification request status.
    If no request has been submitted, has_submitted=False.
    """
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    # Get latest verification request
    verification = db.query(TeacherVerification).filter(
        TeacherVerification.teacher_id == profile.id
    ).order_by(TeacherVerification.submitted_at.desc()).first()

    if not verification:
        return VerificationStatusResponse(has_submitted=False)

    docs = [
        VerificationDocumentResponse(
            id=doc.id,
            document_type=doc.document_type,
            original_filename=doc.original_filename,
            file_size_bytes=doc.file_size_bytes,
            mime_type=doc.mime_type,
            uploaded_at=doc.uploaded_at,
        )
        for doc in verification.documents
    ]

    return VerificationStatusResponse(
        has_submitted=True,
        status=verification.status,
        submitted_at=verification.submitted_at,
        reviewed_at=verification.reviewed_at,
        rejection_reason=verification.rejection_reason,
        documents=docs,
    )


@router.post(
    "/me/verification",
    response_model=VerificationStatusResponse,
    status_code=201,
    summary="Submit verification request with documents",
)
def submit_verification(
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
    document_type: str = Form(..., description="certificate | id_proof | degree | linkedin | other"),
    file: UploadFile = File(..., description="Document file (PDF, JPG, PNG, max 5MB)"),
):
    """
    Submit a teacher verification request with a supporting document.
    Can only submit if no pending request exists.
    After rejection, teacher can resubmit (creates new verification record).

    Note: In production this uploads to GCS private bucket.
    For MVP, stores file metadata only (GCS integration in Component services).
    """
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    # Block resubmission if already pending
    existing = db.query(TeacherVerification).filter(
        and_(
            TeacherVerification.teacher_id == profile.id,
            TeacherVerification.status == "pending",
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A verification request is already pending. Please wait for admin review.",
        )

    # Validate document type
    valid_types = {"certificate", "id_proof", "degree", "linkedin", "other"}
    if document_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid document_type. Must be one of: {', '.join(valid_types)}",
        )

    # Validate file size (5MB limit)
    max_size = 5 * 1024 * 1024
    file_content = file.file.read()
    if len(file_content) > max_size:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 5MB.")
    file.file.seek(0)

    # Create verification record
    verification = TeacherVerification(
        teacher_id=profile.id,
        status="pending",
    )
    db.add(verification)
    db.flush()

    # Create document record
    # In production: upload file_content to GCS private bucket here
    # gcs_path = f"verifications/{profile.id}/{verification.id}/{file.filename}"
    gcs_path = f"verifications/{profile.id}/{verification.id}/{file.filename}"

    doc = VerificationDocument(
        verification_id=verification.id,
        document_type=document_type,
        gcs_path=gcs_path,
        original_filename=file.filename,
        file_size_bytes=len(file_content),
        mime_type=file.content_type,
    )
    db.add(doc)
    db.commit()

    return VerificationStatusResponse(
        has_submitted=True,
        status="pending",
        submitted_at=verification.submitted_at,
        documents=[
            VerificationDocumentResponse(
                id=doc.id,
                document_type=doc.document_type,
                original_filename=doc.original_filename,
                file_size_bytes=doc.file_size_bytes,
                mime_type=doc.mime_type,
                uploaded_at=doc.uploaded_at,
            )
        ],
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
    Commission tiers based on total lifetime revenue:
      0 – 50K rupees    → 20%
      50K – 2L rupees   → 15%
      2L+ rupees        → 10%
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
    )