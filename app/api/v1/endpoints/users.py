# app/api/v1/endpoints/users.py
# User profile endpoints.
#
# Routes:
#   GET  /api/v1/users/me          — own full profile (private)
#   PATCH /api/v1/users/me         — update name, phone, avatar_url
#   GET  /api/v1/users/{user_id}   — public profile (sanitised)
#   POST /api/v1/users/me/avatar   — upload avatar file → GCS → save URL
#
# Rules enforced:
#   - Public view never returns phone, address, or payment info
#   - resolve_user_marks() is called on every profile response
#   - Avatar stored in GCS_PUBLIC_BUCKET, URL saved to users.avatar_url
#   - Teachers: public profile includes subjects, bio, is_verified, total_students
#   - Students: public profile only shows grade, performance_score, badges

import uuid
from typing import Optional

import app.db.base  # noqa: F401 — must import before any DB query (registers all models)
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.dependencies import require_login, resolve_user_marks
from app.db.session import get_db
from app.models.student import StudentProfile
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.schemas.user import (
    AvatarUploadResponse,
    OwnProfileResponse,
    PublicProfileResponse,
    StudentPublicInfo,
    TeacherPublicInfo,
    UpdateProfileRequest,
)

router = APIRouter()

# ── Helpers ───────────────────────────────────────────────────────────────────

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB


def _get_teacher_info(db: Session, user_id: uuid.UUID) -> Optional[TeacherPublicInfo]:
    """Fetch teacher profile sub-object. Returns None if no profile exists."""
    profile = db.execute(
        select(TeacherProfile).where(TeacherProfile.user_id == user_id)
    ).scalar_one_or_none()
    if profile is None:
        return None

    # Count distinct students enrolled in this teacher's batches
    from app.models.class_ import Batch, BatchMember
    total_students: int = db.execute(
        select(func.count(func.distinct(BatchMember.student_id)))
        .join(Batch, Batch.id == BatchMember.batch_id)
        .where(Batch.teacher_id == user_id)
    ).scalar_one()

    subjects: list[str] = profile.subjects or []
    return TeacherPublicInfo(
        subjects=subjects,
        bio=profile.bio,
        is_verified=profile.is_verified,
        total_students=total_students,
    )


def _get_student_info(db: Session, user_id: uuid.UUID) -> Optional[StudentPublicInfo]:
    """Fetch student profile sub-object. Returns None if no profile exists."""
    profile = db.execute(
        select(StudentProfile).where(StudentProfile.user_id == user_id)
    ).scalar_one_or_none()
    if profile is None:
        return None
    return StudentPublicInfo(
        grade=profile.current_grade,
        performance_score=float(profile.performance_score or 0.0),
        badges=profile.badges or [],
    )


def _upload_avatar_to_gcs(file_bytes: bytes, content_type: str, user_id: uuid.UUID) -> str:
    """
    Upload avatar bytes to GCS_PUBLIC_BUCKET and return the public URL.
    Falls back gracefully if GCS is not configured (local dev).
    """
    import os

    from google.cloud import storage as gcs

    bucket_name = os.getenv("GCS_PUBLIC_BUCKET", "")
    if not bucket_name:
        # Local dev fallback — return a placeholder URL
        return f"https://storage.googleapis.com/tamgam-dev/avatars/{user_id}.jpg"

    client = gcs.Client()
    bucket = client.bucket(bucket_name)
    ext = content_type.split("/")[-1]  # jpeg | png | webp
    blob_name = f"avatars/{user_id}.{ext}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(file_bytes, content_type=content_type)
    blob.make_public()
    return blob.public_url


# ── GET /users/me ─────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=OwnProfileResponse,
    summary="Get own profile",
    status_code=status.HTTP_200_OK,
)
def get_own_profile(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> OwnProfileResponse:
    """
    Returns the authenticated user's full private profile.
    Includes phone, subscription status, and role-specific nested info.
    resolve_user_marks() is called to get live subscription / verification flags.
    """
    marks = resolve_user_marks(current_user.id, db)

    teacher_info = None
    student_info = None
    if current_user.role == "teacher":
        teacher_info = _get_teacher_info(db, current_user.id)
    elif current_user.role == "student":
        student_info = _get_student_info(db, current_user.id)

    return OwnProfileResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        avatar_url=current_user.avatar_url,
        role=current_user.role,
        is_active=current_user.is_active,
        is_subscribed=marks["is_subscribed"],
        is_verified_teacher=marks["is_verified_teacher"],
        teacher_info=teacher_info,
        student_info=student_info,
    )


# ── PATCH /users/me ───────────────────────────────────────────────────────────

@router.patch(
    "/me",
    response_model=OwnProfileResponse,
    summary="Update own profile",
    status_code=status.HTTP_200_OK,
)
def update_own_profile(
    body: UpdateProfileRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> OwnProfileResponse:
    """
    Update the authenticated user's name, phone, or avatar_url.
    Only fields present in the request body are modified (partial update).
    Returns the full updated profile.
    """
    if body.full_name is not None:
        current_user.full_name = body.full_name
    if body.phone is not None:
        current_user.phone = body.phone
    if body.avatar_url is not None:
        current_user.avatar_url = body.avatar_url

    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    marks = resolve_user_marks(current_user.id, db)

    teacher_info = None
    student_info = None
    if current_user.role == "teacher":
        teacher_info = _get_teacher_info(db, current_user.id)
    elif current_user.role == "student":
        student_info = _get_student_info(db, current_user.id)

    return OwnProfileResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        avatar_url=current_user.avatar_url,
        role=current_user.role,
        is_active=current_user.is_active,
        is_subscribed=marks["is_subscribed"],
        is_verified_teacher=marks["is_verified_teacher"],
        teacher_info=teacher_info,
        student_info=student_info,
    )


# ── GET /users/{user_id} ──────────────────────────────────────────────────────

@router.get(
    "/{user_id}",
    response_model=PublicProfileResponse,
    summary="Get public profile",
    status_code=status.HTTP_200_OK,
)
def get_public_profile(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> PublicProfileResponse:
    """
    Public profile — no auth required.
    Never returns phone, address, or payment info.
    Teachers: includes subjects, bio, is_verified, total_students.
    Students: includes grade, performance_score, badges only.
    """
    user = db.execute(
        select(User).where(User.id == user_id, User.is_active == True)  # noqa: E712
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    marks = resolve_user_marks(user.id, db)

    teacher_info = None
    student_info = None
    if user.role == "teacher":
        teacher_info = _get_teacher_info(db, user.id)
    elif user.role == "student":
        student_info = _get_student_info(db, user.id)

    return PublicProfileResponse(
        id=user.id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        role=user.role,
        is_subscribed=marks["is_subscribed"],
        is_verified_teacher=marks["is_verified_teacher"],
        teacher_info=teacher_info,
        student_info=student_info,
    )


# ── POST /users/me/avatar ─────────────────────────────────────────────────────

@router.post(
    "/me/avatar",
    response_model=AvatarUploadResponse,
    summary="Upload avatar",
    status_code=status.HTTP_200_OK,
)
async def upload_avatar(
    file: UploadFile = File(..., description="JPEG, PNG, or WebP — max 2 MB"),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> AvatarUploadResponse:
    """
    Upload a new avatar image.
    File is stored in GCS_PUBLIC_BUCKET under avatars/{user_id}.{ext}.
    The public URL is saved to users.avatar_url and returned.
    """
    # Validate content type
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type not allowed. Use JPEG, PNG, or WebP.",
        )

    # Read and validate size
    file_bytes = await file.read()
    if len(file_bytes) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Avatar must be 2 MB or smaller.",
        )

    # Upload to GCS
    public_url = _upload_avatar_to_gcs(file_bytes, file.content_type, current_user.id)

    # Persist URL
    current_user.avatar_url = public_url
    db.add(current_user)
    db.commit()

    return AvatarUploadResponse(avatar_url=public_url)