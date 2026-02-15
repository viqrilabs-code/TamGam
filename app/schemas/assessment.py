# app/schemas/assessment.py
# Pydantic request/response models for adaptive assessment endpoints

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# ── Question Structure ────────────────────────────────────────────────────────
# Stored as JSONB in DB

class MCQOption(BaseModel):
    key: str        # A | B | C | D
    text: str


class Question(BaseModel):
    id: int                         # 1-based index within assessment
    type: str                       # mcq | short_answer
    level: str                      # beginner | developing | proficient | advanced
    question: str
    options: Optional[List[MCQOption]] = None   # Only for mcq
    correct_answer: Optional[str] = None        # Hidden from student in GET response
    explanation: Optional[str] = None           # Shown after submission


class QuestionForStudent(BaseModel):
    """Question shown to student -- no correct_answer or explanation."""
    id: int
    type: str
    level: str
    question: str
    options: Optional[List[MCQOption]] = None


# ── Assessment Response ───────────────────────────────────────────────────────

class AssessmentResponse(BaseModel):
    id: UUID
    class_id: UUID
    status: str                         # pending | generating | completed | failed
    questions: Optional[List[QuestionForStudent]] = None  # Shown to students
    total_questions: Optional[int] = None
    is_gated: bool                       # True = student has no subscription
    created_at: datetime
    updated_at: datetime


class AssessmentTeacherResponse(AssessmentResponse):
    """Full assessment with correct answers -- teacher only."""
    questions_with_answers: Optional[List[Question]] = None


# ── Submission ────────────────────────────────────────────────────────────────

class AnswerSubmission(BaseModel):
    question_id: int
    answer: str


class AssessmentSubmitRequest(BaseModel):
    answers: List[AnswerSubmission]

    @field_validator("answers")
    @classmethod
    def answers_not_empty(cls, v):
        if not v:
            raise ValueError("Must submit at least one answer")
        return v


class AnswerResult(BaseModel):
    question_id: int
    question: str
    your_answer: str
    correct_answer: str
    is_correct: bool
    explanation: Optional[str] = None


class AssessmentSubmitResponse(BaseModel):
    assessment_id: UUID
    class_id: UUID
    score: float                     # 0.0 to 100.0
    correct_count: int
    total_questions: int
    results: List[AnswerResult]
    understanding_level: str         # Current level after this submission
    level_changed: bool              # True if level went up or down
    previous_level: Optional[str] = None


# ── History ───────────────────────────────────────────────────────────────────

class AssessmentHistoryItem(BaseModel):
    id: UUID
    class_id: UUID
    class_title: Optional[str] = None
    subject: Optional[str] = None
    score: float
    correct_count: int
    total_questions: int
    understanding_level: str
    submitted_at: datetime


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str