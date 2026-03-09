from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import get_optional_user
from app.db.session import get_db
from app.models.contact_complaint import ContactComplaint
from app.models.user import User
from app.schemas.contact import ComplaintCreateRequest, ComplaintCreateResponse

router = APIRouter()


@router.post(
    "/complaints",
    response_model=ComplaintCreateResponse,
    status_code=201,
    summary="Submit contact complaint / support message",
)
def create_complaint(
    payload: ComplaintCreateRequest,
    current_user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    complaint = ContactComplaint(
        user_id=current_user.id if current_user else None,
        full_name=payload.full_name.strip(),
        email=str(payload.email).strip().lower(),
        subject=(payload.subject or "").strip() or None,
        message=payload.message.strip(),
        source_page=(payload.source_page or "").strip() or None,
        status="open",
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)

    return ComplaintCreateResponse(
        complaint_id=complaint.id,
        message="Thanks. Your message has been received.",
    )
