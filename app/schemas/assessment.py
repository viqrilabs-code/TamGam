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
    teacher_feedback_text: Optional[str] = None
    teacher_feedback_score: Optional[int] = None
    teacher_feedback_given_at: Optional[datetime] = None
    feedback_provider_name: Optional[str] = None


class AssessmentFeedbackRequest(BaseModel):
    feedback_text: Optional[str] = None
    feedback_score: Optional[int] = None

    @field_validator("feedback_score")
    @classmethod
    def validate_feedback_score(cls, v):
        if v is not None and (v < 0 or v > 100):
            raise ValueError("Feedback score must be between 0 and 100")
        return v


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str


# Profile assessment (student)
class ProfileAssessmentQuestion(BaseModel):
    id: int
    type: str
    difficulty_band: str  # below | same | above
    topic: str
    question: str
    options: List[MCQOption]


class ProfileAssessmentGenerateResponse(BaseModel):
    attempt_token: str
    total_questions: int
    questions: List[ProfileAssessmentQuestion]


class ProfileAssessmentAnswer(BaseModel):
    question_id: int
    answer_key: str


class ProfileAssessmentSubmitRequest(BaseModel):
    attempt_token: str
    answers: List[ProfileAssessmentAnswer]

    @field_validator("answers")
    @classmethod
    def profile_answers_not_empty(cls, v):
        if not v:
            raise ValueError("Must submit at least one answer")
        return v


class ProfileAssessmentSubmitResponse(BaseModel):
    score: float
    correct_count: int
    total_questions: int
    grade_standard: int
    strengths: List[str]
    improvement_areas: List[str]
    understanding_level: int


# Teacher upload-based assessment generation
class TeacherAssessmentQuestion(BaseModel):
    id: int
    type: str  # mcq | subjective
    question: str
    options: Optional[List[MCQOption]] = None
    answer: Optional[str] = None
    explanation: Optional[str] = None


class TeacherGeneratedAssessmentResponse(BaseModel):
    class_id: UUID
    total_questions: int
    mcq_count: int
    subjective_count: int
    questions: List[TeacherAssessmentQuestion]
