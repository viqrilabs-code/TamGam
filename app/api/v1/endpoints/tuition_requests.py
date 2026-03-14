# app/api/v1/endpoints/tuition_requests.py
# Tuition request endpoints
#
# Student flow:
#   POST   /tuition-requests/           â†’ raise request to a teacher
#   GET    /tuition-requests/me         â†’ see own sent requests
#   DELETE /tuition-requests/{id}       â†’ cancel a pending request
#
# Teacher flow:
#   GET    /tuition-requests/incoming   â†’ see pending requests from students
#   PATCH  /tuition-requests/{id}/accept  â†’ accept â†’ auto-creates enrollment
#   PATCH  /tuition-requests/{id}/decline â†’ decline with optional reason
#
# Teacher searches for students:
#   GET    /tuition-requests/students/search â†’ filter by grade/subject/city

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.config import settings
from app.core.dependencies import (
    ensure_teacher_billing_active,
    get_effective_active_subscription,
    require_login,
)
from app.db.session import get_db
from app.models.student import Batch, BatchMember, Enrollment, StudentProfile
from app.models.notification import Notification
from app.models.teacher import TeacherProfile
from app.models.tuition_request import TuitionRequest
from app.models.user import User
from app.schemas.tuition_request import (
    BatchCheckoutResponse,
    BatchPaymentInitRequest,
    BatchPaymentInitResponse,
    MessageResponse,
    StudentSearchItem,
    TeacherStudentItem,
    TuitionRequestCreate,
    TuitionRequestDecline,
    TuitionRequestListItem,
    TuitionRequestResponse,
)
from app.services import razorpay_service

router = APIRouter()


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _is_subscribed(user_id: UUID, db: Session) -> bool:
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.role == "student":
        return True
    return get_effective_active_subscription(user_id, db) is not None


def _teacher_commission_rate(total_revenue_paise: int) -> float:
    return 5.0


def _sync_due_unenrollments(db: Session, *, student_profile_id: UUID | None = None, teacher_id: UUID | None = None) -> None:
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


def _resolve_student_batch_checkout(
    *,
    batch_id: UUID,
    student_profile: StudentProfile,
    db: Session,
) -> tuple[Batch, TeacherProfile, User, int | None]:
    batch = db.query(Batch).filter(
        and_(
            Batch.id == batch_id,
            Batch.is_active == True,
            Batch.student_selection_enabled == True,
        )
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Selected batch is unavailable.")

    teacher_profile = db.query(TeacherProfile).filter(TeacherProfile.id == batch.teacher_id).first()
    if not teacher_profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    teacher_user = db.query(User).filter(
        and_(
            User.id == teacher_profile.user_id,
            User.is_active == True,
        )
    ).first()
    if not teacher_user:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    seats_left: int | None = None
    if batch.max_students is not None:
        member_count = db.query(BatchMember).filter(BatchMember.batch_id == batch.id).count()
        seats_left = int(batch.max_students) - member_count
        if seats_left <= 0:
            raise HTTPException(status_code=409, detail="Selected batch is full.")

    if batch.grade_level is not None and student_profile.grade is not None:
        if int(batch.grade_level) != int(student_profile.grade):
            raise HTTPException(status_code=409, detail="Selected batch is not available for your class.")

    return batch, teacher_profile, teacher_user, seats_left


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

    batch = db.query(Batch).filter(Batch.id == req.batch_id).first() if req.batch_id else None

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
        batch_id=req.batch_id,
        batch_name=batch.name if batch else None,
        subject=req.subject,
        message=req.message,
        decline_reason=req.decline_reason,
        enrollment_id=req.enrollment_id,
        created_at=req.created_at,
        responded_at=req.responded_at,
    )


# â”€â”€ Student Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/batches/{batch_id}/checkout",
    response_model=BatchCheckoutResponse,
    summary="Get batch checkout details for student",
)
def get_batch_checkout(
    batch_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    student_profile = _get_student_profile(current_user.id, db)
    batch, teacher_profile, teacher_user, seats_left = _resolve_student_batch_checkout(
        batch_id=batch_id,
        student_profile=student_profile,
        db=db,
    )
    return BatchCheckoutResponse(
        batch_id=batch.id,
        batch_name=batch.name,
        subject=batch.subject,
        description=batch.description,
        grade_level=batch.grade_level,
        class_timing=batch.default_timing,
        class_days=batch.class_days or [],
        max_students=batch.max_students,
        seats_left=seats_left,
        fee_paise=int(batch.fee_paise or 0),
        fee_rupees=(batch.fee_paise or 0) / 100,
        teacher_id=teacher_profile.id,
        teacher_name=teacher_user.full_name,
        teacher_avatar_url=teacher_user.avatar_url,
        teacher_is_verified=teacher_profile.is_verified,
        teacher_profile_url=f"/teacher-profile.html?teacher_id={teacher_profile.id}",
    )


@router.post(
    "/batches/{batch_id}/payment/init",
    response_model=BatchPaymentInitResponse,
    summary="Create payment link for joining a batch",
)
def init_batch_payment(
    batch_id: UUID,
    payload: BatchPaymentInitRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    student_profile = _get_student_profile(current_user.id, db)
    batch, teacher_profile, teacher_user, _ = _resolve_student_batch_checkout(
        batch_id=batch_id,
        student_profile=student_profile,
        db=db,
    )
    subject_text = (payload.subject or batch.subject or "").strip()
    if not subject_text:
        raise HTTPException(status_code=422, detail="Subject is required for checkout.")

    already_enrolled = db.query(Enrollment).filter(
        and_(
            Enrollment.student_id == student_profile.id,
            Enrollment.teacher_id == teacher_profile.id,
            Enrollment.subject == subject_text,
            Enrollment.is_active == True,
        )
    ).first()
    if already_enrolled:
        raise HTTPException(status_code=409, detail=f"Already enrolled for {subject_text}.")

    existing_pending = db.query(TuitionRequest).filter(
        and_(
            TuitionRequest.student_id == student_profile.id,
            TuitionRequest.teacher_id == teacher_profile.id,
            TuitionRequest.batch_id == batch.id,
            TuitionRequest.subject == subject_text,
            TuitionRequest.status == "pending",
        )
    ).first()
    if existing_pending:
        raise HTTPException(status_code=409, detail="A pending request for this batch already exists.")

    amount_paise = int(batch.fee_paise or 0)
    if amount_paise <= 0:
        return BatchPaymentInitResponse(
            payment_link="",
            amount_paise=0,
            amount_rupees=0.0,
            currency="INR",
            mode="free",
            message="This batch currently has no fee configured.",
        )

    has_keys = razorpay_service.has_credentials()
    if not has_keys:
        return BatchPaymentInitResponse(
            payment_link="https://rzp.io/mock-checkout",
            amount_paise=amount_paise,
            amount_rupees=amount_paise / 100,
            currency="INR",
            mode="mock",
            message="Razorpay is not configured in this environment. Use mock checkout for local testing.",
        )

    callback_url = (payload.callback_url or "").strip() or None
    try:
        link = razorpay_service.create_payment_link(
            amount_paise=amount_paise,
            description=f"tamgam batch join: {batch.name}",
            customer_name=current_user.full_name,
            customer_email=current_user.email,
            callback_url=callback_url,
            notes={
                "kind": "batch_join",
                "batch_id": str(batch.id),
                "teacher_id": str(teacher_profile.id),
                "teacher_name": teacher_user.full_name,
                "student_user_id": str(current_user.id),
                "subject": subject_text,
            },
        )
        payment_link = str(link.get("short_url") or "").strip()
        if not payment_link:
            raise RuntimeError("Razorpay did not return a hosted payment link.")
        return BatchPaymentInitResponse(
            payment_link=payment_link,
            amount_paise=amount_paise,
            amount_rupees=amount_paise / 100,
            currency="INR",
            mode="razorpay",
            message="Payment link created.",
        )
    except Exception as exc:
        if settings.app_env == "production":
            raise HTTPException(status_code=502, detail=f"Failed to create payment link: {exc}") from exc
        return BatchPaymentInitResponse(
            payment_link="https://rzp.io/mock-checkout",
            amount_paise=amount_paise,
            amount_rupees=amount_paise / 100,
            currency="INR",
            mode="mock",
            message=f"Razorpay link failed in dev mode ({exc}). Falling back to mock checkout.",
        )


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
    Subscription is not required to raise the request.
    Cannot send duplicate pending requests to the same teacher for the same subject.
    """
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    student_profile = _get_student_profile(current_user.id, db)
    _sync_due_unenrollments(db, student_profile_id=student_profile.id)

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

    selected_batch = None
    if payload.batch_id:
        selected_batch, _, _, _ = _resolve_student_batch_checkout(
            batch_id=payload.batch_id,
            student_profile=student_profile,
            db=db,
        )
        if selected_batch.teacher_id != teacher_profile.id:
            raise HTTPException(status_code=409, detail="Selected batch does not belong to this teacher.")

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
        batch_id=payload.batch_id,
        subject=payload.subject,
        message=payload.message,
        grade=student_profile.grade,
        status="pending",
    )
    db.add(req)
    # Notify teacher about new tuition request
    db.add(Notification(
        user_id=teacher_profile.user_id,
        notification_type="announcement",
        title="New tuition request",
        body=(
            f"{current_user.full_name} (Class {student_profile.grade or '-'}) requested tuition for {payload.subject}."
            + (f" Batch: {selected_batch.name}." if selected_batch else "")
            + (f" City: {student_profile.city}." if student_profile.city else "")
            + (f" State: {student_profile.state}." if student_profile.state else "")
        ),
        action_url="/teacher-dashboard.html#tuition-requests",
        extra_data={
            "kind": "tuition_request",
            "request_id": str(req.id),
            "student_id": str(student_profile.id),
            "subject": payload.subject,
            "batch_id": str(selected_batch.id) if selected_batch else None,
            "batch_name": selected_batch.name if selected_batch else None,
            "student_grade": student_profile.grade,
            "student_city": student_profile.city,
            "student_state": student_profile.state,
            "student_learning_goals": student_profile.learning_goals,
        },
        is_read=False,
    ))
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

    query = db.query(TuitionRequest, TeacherProfile, User, Batch).join(
        TeacherProfile, TeacherProfile.id == TuitionRequest.teacher_id
    ).join(
        User, User.id == TeacherProfile.user_id
    ).outerjoin(
        Batch, Batch.id == TuitionRequest.batch_id
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
            batch_id=req.batch_id,
            batch_name=batch.name if batch else None,
            message=req.message,
            decline_reason=req.decline_reason,
            enrollment_id=req.enrollment_id,
            created_at=req.created_at,
            responded_at=req.responded_at,
            counterparty_id=teacher_profile.id,
            counterparty_name=teacher_user.full_name,
            counterparty_avatar_url=teacher_user.avatar_url,
            counterparty_is_verified=teacher_profile.is_verified,
            counterparty_city=None,
            counterparty_state=None,
            counterparty_learning_goals=None,
        )
        for req, teacher_profile, teacher_user, batch in results
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


# â”€â”€ Teacher Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

    query = db.query(TuitionRequest, StudentProfile, User, Batch).join(
        StudentProfile, StudentProfile.id == TuitionRequest.student_id
    ).join(
        User, User.id == StudentProfile.user_id
    ).outerjoin(
        Batch, Batch.id == TuitionRequest.batch_id
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
            batch_id=req.batch_id,
            batch_name=batch.name if batch else None,
            message=req.message,
            decline_reason=req.decline_reason,
            enrollment_id=req.enrollment_id,
            created_at=req.created_at,
            responded_at=req.responded_at,
            counterparty_id=student_profile.id,
            counterparty_name=student_user.full_name,
            counterparty_avatar_url=student_user.avatar_url,
            counterparty_grade=student_profile.grade,
            counterparty_city=student_profile.city,
            counterparty_state=student_profile.state,
            counterparty_learning_goals=student_profile.learning_goals,
        )
        for req, student_profile, student_user, batch in results
    ]


@router.get(
    "/my-students",
    response_model=List[TeacherStudentItem],
    summary="Teacher sees actively enrolled students",
)
def list_my_students(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """List unique students currently enrolled with this teacher."""
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher access only.")

    teacher_profile = _get_teacher_profile(current_user.id, db)
    _sync_due_unenrollments(db, teacher_id=teacher_profile.id)

    rows = db.query(Enrollment, StudentProfile, User).join(
        StudentProfile, StudentProfile.id == Enrollment.student_id
    ).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        and_(
            Enrollment.teacher_id == teacher_profile.id,
            Enrollment.is_active == True,
        )
    ).order_by(Enrollment.enrolled_at.desc()).all()

    grouped = {}
    for enrollment, student_profile, student_user in rows:
        key = str(student_profile.id)
        if key not in grouped:
            grouped[key] = TeacherStudentItem(
                student_id=student_profile.id,
                user_id=student_user.id,
                full_name=student_user.full_name,
                avatar_url=student_user.avatar_url,
                grade=student_profile.grade,
                city=student_profile.city,
                state=student_profile.state,
                is_subscribed=_is_subscribed(student_user.id, db),
                enrolled_subjects=[],
                latest_enrolled_at=enrollment.enrolled_at,
            )
        if enrollment.subject and enrollment.subject not in grouped[key].enrolled_subjects:
            grouped[key].enrolled_subjects.append(enrollment.subject)

    return list(grouped.values())


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
    Enrollment is created immediately (student access is free).
    """
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher access only.")
    ensure_teacher_billing_active(current_user, db)

    teacher_profile = _get_teacher_profile(current_user.id, db)
    _sync_due_unenrollments(db, teacher_id=teacher_profile.id)
    student_profile = None

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

    student_profile = db.query(StudentProfile).filter(
        StudentProfile.id == req.student_id
    ).first()
    if not student_profile:
        raise HTTPException(status_code=404, detail="Student profile not found.")
    _sync_due_unenrollments(db, student_profile_id=student_profile.id, teacher_id=teacher_profile.id)

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

    enrollment = Enrollment(
        student_id=req.student_id,
        teacher_id=teacher_profile.id,
        subject=req.subject,
        is_active=True,
    )
    db.add(enrollment)
    db.flush()  # Get enrollment.id before commit

    # Accrue tracked teacher income from the selected batch fee.
    gross = 0
    if req.batch_id:
        selected_batch = db.query(Batch).filter(
            and_(
                Batch.id == req.batch_id,
                Batch.teacher_id == teacher_profile.id,
            )
        ).first()
        gross = int(selected_batch.fee_paise or 0) if selected_batch else 0
    if gross > 0:
        teacher_profile.total_revenue_paise = int(teacher_profile.total_revenue_paise or 0) + gross
        rate = _teacher_commission_rate(int(teacher_profile.total_revenue_paise or 0))
        commission = int(round(gross * rate / 100.0))
        teacher_profile.platform_commission_paise = int(teacher_profile.platform_commission_paise or 0) + commission

    # Update request
    req.status = "accepted"
    req.enrollment_id = enrollment.id
    req.responded_at = datetime.now(timezone.utc)
    req.updated_at = datetime.now(timezone.utc)

    # Mark teacher's request notification as handled.
    teacher_notif = db.query(Notification).filter(
        and_(
            Notification.user_id == current_user.id,
            Notification.notification_type == "announcement",
            Notification.is_read == False,
            Notification.extra_data["kind"].astext == "tuition_request",
            Notification.extra_data["request_id"].astext == str(req.id),
        )
    ).first()
    if teacher_notif:
        teacher_notif.is_read = True

    # Notify student on acceptance.
    db.add(Notification(
        user_id=student_profile.user_id,
        notification_type="announcement",
        title="Tuition request accepted",
        body=(
            f"Your tuition request for {req.subject} was accepted by the teacher. "
            "You are now enrolled and can attend classes from your dashboard."
        ),
        action_url="/dashboard.html",
        extra_data={
            "kind": "tuition_request_accepted",
            "request_id": str(req.id),
            "teacher_id": str(teacher_profile.id),
            "subject": req.subject,
            "tracked_fee_paise": gross,
        },
        is_read=False,
    ))

    # Update teacher's student count
    teacher_profile.total_students = db.query(Enrollment).filter(
        and_(
            Enrollment.teacher_id == teacher_profile.id,
            Enrollment.is_active == True,
        )
    ).count()

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
    ensure_teacher_billing_active(current_user, db)

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

    # Mark teacher's request notification as handled.
    teacher_notif = db.query(Notification).filter(
        and_(
            Notification.user_id == current_user.id,
            Notification.notification_type == "announcement",
            Notification.is_read == False,
            Notification.extra_data["kind"].astext == "tuition_request",
            Notification.extra_data["request_id"].astext == str(req.id),
        )
    ).first()
    if teacher_notif:
        teacher_notif.is_read = True

    # Notify student on decline with optional reason.
    student_profile = db.query(StudentProfile).filter(
        StudentProfile.id == req.student_id
    ).first()
    if student_profile:
        reason = f" Note: {payload.decline_reason}" if payload.decline_reason else ""
        db.add(Notification(
            user_id=student_profile.user_id,
            notification_type="announcement",
            title="Tuition request declined",
            body=(
                f"Your tuition request for {req.subject} was declined by the teacher due to time constraints. "
                f"Please try another teacher.{reason}"
            ),
            action_url="/find-teachers.html",
            extra_data={
                "kind": "tuition_request_declined",
                "request_id": str(req.id),
                "teacher_id": str(teacher_profile.id),
                "subject": req.subject,
            },
            is_read=False,
        ))
    db.commit()
    db.refresh(req)

    return _build_response(req, db)


# â”€â”€ Teacher: Search Students â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    subscribed_only: bool = Query(False, description="Legacy filter; students are free by default."),
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

    subscribed_user_ids = {user.id for _, user in results if _is_subscribed(user.id, db)}

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

