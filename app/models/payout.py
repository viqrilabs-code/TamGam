# app/models/payout.py
# Teacher payout ledger for monthly settlement and disbursement tracking.

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class TeacherPayout(Base):
    __tablename__ = "teacher_payouts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)

    gross_revenue_paise = Column(Integer, nullable=True)
    platform_commission_paise = Column(Integer, nullable=True)
    net_amount_paise = Column(Integer, nullable=False)

    status = Column(
        Enum(
            "pending",
            "processing",
            "paid",
            "failed",
            name="teacher_payout_status_enum",
        ),
        nullable=False,
        default="pending",
        index=True,
    )

    razorpay_payout_id = Column(String(255), nullable=True, unique=True, index=True)
    razorpay_status = Column(String(100), nullable=True)
    failure_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)

    teacher = relationship("TeacherProfile", back_populates="payouts")

    def __repr__(self) -> str:
        return f"<TeacherPayout teacher={self.teacher_id} net={self.net_amount_paise} status={self.status}>"
