import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class ContactComplaint(Base):
    __tablename__ = "contact_complaints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    subject = Column(String(255), nullable=True)
    message = Column(Text, nullable=False)
    source_page = Column(String(255), nullable=True)

    status = Column(
        Enum("open", "in_progress", "resolved", "closed", name="complaint_status_enum"),
        nullable=False,
        default="open",
        index=True,
    )
    admin_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

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

    def __repr__(self) -> str:
        return f"<ContactComplaint id={self.id} email={self.email} status={self.status}>"
