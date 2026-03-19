from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class GroupStudyCreatePayload(BaseModel):
    title: str
    subject: str
    scheduled_at: datetime
    duration_minutes: int = 60
    batch_id: Optional[UUID] = None
    participant_user_ids: List[UUID] = Field(default_factory=list)
    group_discussion_enabled: bool = False
    topic_outline: Optional[str] = None
    document_name: Optional[str] = None
    document_text: Optional[str] = None

    @field_validator("title", "subject")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be empty.")
        return value

    @field_validator("duration_minutes")
    @classmethod
    def _duration_bounds(cls, value: int) -> int:
        if value < 15 or value > 240:
            raise ValueError("duration_minutes must be between 15 and 240")
        return value

    @model_validator(mode="after")
    def _require_discussion_source(self):
        topic = (self.topic_outline or "").strip()
        document_text = (self.document_text or "").strip()
        if not topic and not document_text:
            raise ValueError("Provide either a topic outline or an uploaded discussion document.")
        self.topic_outline = topic or None
        self.document_text = document_text or None
        self.document_name = (self.document_name or "").strip() or None
        return self


class GroupStudySubmitKeyRequest(BaseModel):
    gemini_api_key: str

    @field_validator("gemini_api_key")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 20 or len(value) > 200:
            raise ValueError("Invalid Gemini API key format.")
        return value


class GroupStudyAnswerRequest(BaseModel):
    answer_text: Optional[str] = None
    answer_choice: Optional[str] = None

    @model_validator(mode="after")
    def _require_answer(self):
        answer_text = (self.answer_text or "").strip()
        answer_choice = (self.answer_choice or "").strip().upper()
        if not answer_text and not answer_choice:
            raise ValueError("Provide answer_text or answer_choice.")
        self.answer_text = answer_text or None
        self.answer_choice = answer_choice or None
        return self


class GroupStudyStudentSearchItem(BaseModel):
    user_id: UUID
    student_id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    grade: Optional[int] = None
    school_name: Optional[str] = None


class GroupStudyTurnOption(BaseModel):
    key: str
    text: str


class GroupStudyTurnResponse(BaseModel):
    id: UUID
    turn_index: int
    section_index: int
    turn_type: str
    section_title: Optional[str] = None
    target_user_id: Optional[UUID] = None
    target_name: Optional[str] = None
    prompt_text: str
    question_text: Optional[str] = None
    source_excerpt: Optional[str] = None
    difficulty_level: Optional[str] = None
    time_limit_seconds: Optional[int] = None
    expires_at: Optional[datetime] = None
    options: List[GroupStudyTurnOption] = Field(default_factory=list)
    answer_text: Optional[str] = None
    answer_choice: Optional[str] = None
    evaluation_data: Optional[Dict[str, Any]] = None
    score_awarded: Optional[float] = None
    is_correct: Optional[bool] = None
    status: str
    created_at: datetime
    answered_at: Optional[datetime] = None


class GroupStudyParticipantResponse(BaseModel):
    user_id: UUID
    student_id: Optional[UUID] = None
    full_name: str
    avatar_url: Optional[str] = None
    grade: Optional[int] = None
    role: str
    invite_source: str
    status: str
    has_submitted_api_key: bool
    joined_at: Optional[datetime] = None
    total_score: float = 0.0
    total_questions: int = 0
    correct_answers: int = 0
    participation_count: int = 0


class GroupStudyReportParticipantResponse(BaseModel):
    user_id: UUID
    full_name: str
    score: float
    total_questions: int
    correct_answers: int
    participation_count: int
    strengths: List[str] = Field(default_factory=list)
    improvement_areas: List[str] = Field(default_factory=list)


class GroupStudyReportResponse(BaseModel):
    generated_at: datetime
    winner_user_id: Optional[UUID] = None
    winner_name: Optional[str] = None
    summary: str
    share_caption: str
    participants: List[GroupStudyReportParticipantResponse] = Field(default_factory=list)


class GroupStudySummaryResponse(BaseModel):
    id: UUID
    title: str
    subject: str
    creator_role: str
    batch_id: Optional[UUID] = None
    batch_name: Optional[str] = None
    status: str
    scheduled_at: datetime
    duration_minutes: int
    participant_count: int
    join_available: bool
    current_turn_type: Optional[str] = None
    current_target_name: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class GroupStudyListResponse(BaseModel):
    studies: List[GroupStudySummaryResponse]
    total: int


class GroupStudyDetailResponse(GroupStudySummaryResponse):
    creator_user_id: UUID
    description: Optional[str] = None
    document_name: Optional[str] = None
    group_discussion_enabled: bool
    stop_reason: Optional[str] = None
    stop_request_active: bool = False
    stop_request_reason: Optional[str] = None
    stop_request_requested_by_name: Optional[str] = None
    stop_request_approvals: int = 0
    stop_request_required: int = 0
    current_user_has_approved_stop: bool = False
    stop_request_pending_names: List[str] = Field(default_factory=list)
    is_creator: bool
    current_user_is_participant: bool
    current_user_has_api_key: bool
    can_start: bool
    can_advance: bool
    can_stop: bool
    current_turn: Optional[GroupStudyTurnResponse] = None
    history: List[GroupStudyTurnResponse] = Field(default_factory=list)
    participants: List[GroupStudyParticipantResponse] = Field(default_factory=list)
    report: Optional[GroupStudyReportResponse] = None


class MessageResponse(BaseModel):
    message: str
