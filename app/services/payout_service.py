# app/services/payout_service.py
# Teacher payout settlement and Razorpay disbursement helpers.

from datetime import datetime, timedelta, timezone
from typing import List, Tuple
from uuid import UUID

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payout import TeacherPayout
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.services import razorpay_service


PAYOUT_RESERVED_STATUSES = ("pending", "processing", "paid")


def previous_month_window(now: datetime | None = None) -> Tuple[datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    first_of_current = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
    last_of_previous = first_of_current - timedelta(microseconds=1)
    first_of_previous = datetime(last_of_previous.year, last_of_previous.month, 1, tzinfo=timezone.utc)
    return first_of_previous, last_of_previous


def _reserved_net_paise(teacher_id: UUID, db: Session) -> int:
    amount = db.query(func.sum(TeacherPayout.net_amount_paise)).filter(
        and_(
            TeacherPayout.teacher_id == teacher_id,
            TeacherPayout.status.in_(PAYOUT_RESERVED_STATUSES),
        )
    ).scalar()
    return int(amount or 0)


def unpaid_net_paise(profile: TeacherProfile, db: Session) -> int:
    net_total = int((profile.total_revenue_paise or 0) - (profile.platform_commission_paise or 0))
    reserved = _reserved_net_paise(profile.id, db)
    return max(0, net_total - reserved)


def create_monthly_settlement_rows(db: Session, *, now: datetime | None = None) -> List[TeacherPayout]:
    period_start, period_end = previous_month_window(now=now)
    created: List[TeacherPayout] = []

    teachers = db.query(TeacherProfile).all()
    for teacher in teachers:
        has_open = db.query(TeacherPayout).filter(
            and_(
                TeacherPayout.teacher_id == teacher.id,
                TeacherPayout.status.in_(("pending", "processing")),
            )
        ).first()
        if has_open:
            continue

        amount = unpaid_net_paise(teacher, db)
        if amount <= 0:
            continue

        payout = TeacherPayout(
            teacher_id=teacher.id,
            period_start=period_start,
            period_end=period_end,
            gross_revenue_paise=None,
            platform_commission_paise=None,
            net_amount_paise=amount,
            status="pending",
        )
        db.add(payout)
        db.flush()
        created.append(payout)
    return created


def _ensure_teacher_fund_account(teacher: TeacherProfile, teacher_user: User, db: Session) -> str:
    if teacher.razorpay_fund_account_id:
        return teacher.razorpay_fund_account_id

    contact_id = teacher.razorpay_contact_id
    if not contact_id:
        contact = razorpay_service.create_contact(
            name=teacher_user.full_name or "tamgam Teacher",
            email=teacher_user.email,
            reference_id=f"teacher_{teacher.id}",
        )
        contact_id = contact.get("id")
        if not contact_id:
            raise RuntimeError("Razorpay contact creation failed (missing id).")
        teacher.razorpay_contact_id = contact_id

    fund = None
    if teacher.bank_upi_id:
        fund = razorpay_service.create_fund_account_upi(
            contact_id=contact_id,
            name=teacher.bank_account_name or teacher_user.full_name or "tamgam Teacher",
            vpa=teacher.bank_upi_id,
        )
    elif teacher.bank_account_number and teacher.bank_ifsc_code and (teacher.bank_account_name or teacher_user.full_name):
        fund = razorpay_service.create_fund_account_bank(
            contact_id=contact_id,
            name=teacher.bank_account_name or teacher_user.full_name,
            ifsc=teacher.bank_ifsc_code,
            account_number=teacher.bank_account_number,
        )
    else:
        raise RuntimeError(
            "Teacher payout details are incomplete. Add UPI or bank account details in teacher profile."
        )

    fund_id = (fund or {}).get("id")
    if not fund_id:
        raise RuntimeError("Razorpay fund account creation failed (missing id).")

    teacher.razorpay_fund_account_id = fund_id
    db.flush()
    return fund_id


def trigger_pending_payouts(db: Session) -> List[TeacherPayout]:
    if not settings.razorpayx_account_number:
        raise RuntimeError("RAZORPAYX_ACCOUNT_NUMBER is not configured.")

    pending = db.query(TeacherPayout).filter(TeacherPayout.status == "pending").order_by(TeacherPayout.created_at.asc()).all()
    processed: List[TeacherPayout] = []

    for payout in pending:
        teacher = db.query(TeacherProfile).filter(TeacherProfile.id == payout.teacher_id).first()
        if not teacher:
            payout.status = "failed"
            payout.failure_reason = "Teacher profile not found."
            payout.processed_at = datetime.now(timezone.utc)
            db.flush()
            processed.append(payout)
            continue

        teacher_user = db.query(User).filter(User.id == teacher.user_id).first()
        if not teacher_user:
            payout.status = "failed"
            payout.failure_reason = "Teacher user not found."
            payout.processed_at = datetime.now(timezone.utc)
            db.flush()
            processed.append(payout)
            continue

        try:
            fund_account_id = _ensure_teacher_fund_account(teacher, teacher_user, db)
            rz = razorpay_service.create_payout(
                source_account_number=settings.razorpayx_account_number,
                fund_account_id=fund_account_id,
                amount_paise=payout.net_amount_paise,
                reference_id=str(payout.id),
                narration=f"tamgam payout {teacher_user.full_name}",
            )

            payout.razorpay_payout_id = rz.get("id")
            payout.razorpay_status = rz.get("status")
            payout.processed_at = datetime.now(timezone.utc)

            rz_status = (rz.get("status") or "").lower()
            if rz_status in {"processed"}:
                payout.status = "paid"
            elif rz_status in {"queued", "pending", "processing"}:
                payout.status = "processing"
            else:
                payout.status = "failed"
                payout.failure_reason = f"Unexpected Razorpay payout status: {rz_status or 'unknown'}"
        except Exception as exc:
            payout.status = "failed"
            payout.failure_reason = str(exc)
            payout.processed_at = datetime.now(timezone.utc)

        db.flush()
        processed.append(payout)

    return processed
