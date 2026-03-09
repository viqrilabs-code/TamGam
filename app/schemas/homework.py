from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class HomeworkResponse(BaseModel):
    id: UUID
    class_id: UUID
    class_title: str
    teacher_id: UUID
    teacher_name: Optional[str] = None
    target_student_id: Optional[UUID] = None
    target_student_name: Optional[str] = None
    kind: str = "assignment"  # assignment | pre_reading | running_notes | solution
    generated_by_diya: bool = False
    title: str
    description: Optional[str] = None
    due_at: Optional[datetime] = None
    has_file: bool
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    created_at: datetime


class HomeworkFeedItem(BaseModel):
    source_type: str  # homework | assessment
    id: str
    class_id: UUID
    class_title: str
    teacher_name: Optional[str] = None
    target_student_id: Optional[UUID] = None
    kind: str = "assignment"  # assignment | pre_reading | running_notes | solution
    generated_by_diya: bool = False
    is_submittable: bool = True
    title: str
    description: Optional[str] = None
    due_at: Optional[datetime] = None
    has_file: bool = False
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    submission_id: Optional[str] = None
    submission_status: Optional[str] = None  # not_submitted | submitted
    submitted_at: Optional[datetime] = None
    feedback_text: Optional[str] = None
    feedback_score: Optional[int] = None
    feedback_given_at: Optional[datetime] = None
    feedback_provider_name: Optional[str] = None
    created_at: datetime


class HomeworkFeedResponse(BaseModel):
    items: List[HomeworkFeedItem]


class HomeworkSubmissionResponse(BaseModel):
    id: UUID
    homework_id: UUID
    class_id: UUID
    class_title: str
    student_id: UUID
    student_name: str
    submission_text: Optional[str] = None
    has_file: bool
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    feedback_text: Optional[str] = None
    feedback_score: Optional[int] = None
    feedback_given_at: Optional[datetime] = None
    feedback_provider_name: Optional[str] = None
    submitted_at: datetime


class HomeworkSubmissionListResponse(BaseModel):
    submissions: List[HomeworkSubmissionResponse]


class DiyaGeneratedHomeworkItem(BaseModel):
    homework_id: UUID
    student_id: UUID
    student_name: str
    title: str


class DiyaGenerateHomeworkResponse(BaseModel):
    class_id: UUID
    generated_count: int
    items: List[DiyaGeneratedHomeworkItem]
