# app/models/feature_usage.py
# Tracks per-user monthly feature usage for plan limit enforcement.

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class FeatureUsage(Base):
    __tablename__ = "feature_usages"
    __table_args__ = (
        UniqueConstraint("user_id", "feature_key", "period_start", name="uq_feature_usage_user_key_period"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_key = Column(String(100), nullable=False, index=True)
    period_start = Column(Date, nullable=False, default=lambda: date.today().replace(day=1))
    usage_count = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User")
