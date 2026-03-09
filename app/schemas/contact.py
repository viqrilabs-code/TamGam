from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class ComplaintCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    subject: Optional[str] = Field(None, max_length=255)
    message: str = Field(..., min_length=10, max_length=5000)
    source_page: Optional[str] = Field(None, max_length=255)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        value = (v or "").strip()
        if len(value) < 2:
            raise ValueError("full_name must be at least 2 characters")
        return value

    @field_validator("message")
    @classmethod
    def clean_message(cls, v: str) -> str:
        value = (v or "").strip()
        if len(value) < 10:
            raise ValueError("message must be at least 10 characters")
        return value


class ComplaintCreateResponse(BaseModel):
    complaint_id: UUID
    message: str


class ComplaintAdminItem(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None
    full_name: str
    email: str
    subject: Optional[str] = None
    message: str
    source_page: Optional[str] = None
    status: str
    admin_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ComplaintAdminUpdateRequest(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None
