# app/schemas/tutor.py
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, field_validator


class SourceCitation(BaseModel):
    label: str       # e.g. "NCERT Class 10 – Polynomials"
    type: str        # e.g. "ncert_book", "book_chunk", "transcript_chunk"


class TutorMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[datetime] = None
    sources: List[SourceCitation] = []


class TutorAskRequest(BaseModel):
    question: str
    session_id: Optional[UUID] = None
    class_id: Optional[UUID] = None
    gemini_api_key: Optional[str] = None

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty")
        if len(v) > 2000:
            raise ValueError("Question too long (max 2000 characters)")
        return v

    @field_validator("gemini_api_key")
    @classmethod
    def validate_api_key(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = v.strip()
        if not value:
            return None
        if len(value) < 20 or len(value) > 200:
            raise ValueError("Invalid Gemini API key format")
        return value


class TutorAskResponse(BaseModel):
    session_id: UUID
    answer: str
    sources_used: int
    understanding_level: int
    sources: List[SourceCitation] = []
    tokens_used: Optional[int] = None


class TutorSessionSummary(BaseModel):
    id: UUID
    class_id: Optional[UUID] = None
    class_title: Optional[str] = None
    message_count: int
    last_message_at: datetime
    created_at: datetime


class TutorSessionDetail(BaseModel):
    id: UUID
    class_id: Optional[UUID] = None
    class_title: Optional[str] = None
    messages: List[TutorMessage]
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    message: str
