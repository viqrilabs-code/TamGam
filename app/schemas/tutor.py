# app/schemas/tutor.py
# Pydantic request/response models for AI Tutor (Diya) endpoints

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# ── Message ───────────────────────────────────────────────────────────────────

class TutorMessage(BaseModel):
    role: str        # user | assistant
    content: str
    timestamp: Optional[datetime] = None


# ── Ask ───────────────────────────────────────────────────────────────────────

class TutorAskRequest(BaseModel):
    question: str
    session_id: Optional[UUID] = None    # Continue existing session, or None to start new
    class_id: Optional[UUID] = None      # Scope RAG to a specific class's notes

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty")
        if len(v) > 2000:
            raise ValueError("Question too long (max 2000 characters)")
        return v


class TutorAskResponse(BaseModel):
    session_id: UUID
    answer: str
    sources_used: int            # How many note chunks were used as context
    understanding_level: int     # 1-5, used to calibrate response style
    tokens_used: Optional[int] = None


# ── Session ───────────────────────────────────────────────────────────────────

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


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str