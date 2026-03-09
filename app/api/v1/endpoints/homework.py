from datetime import datetime, timezone
import json
from io import BytesIO
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, aliased

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_teacher
from app.db.session import get_db
from app.models.assessment import StudentAssessment, StudentUnderstandingProfile
from app.models.class_ import Class
from app.models.homework import Homework, HomeworkSubmission
from app.models.notification import Notification
from app.models.student import BatchMember, Enrollment, StudentProfile
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.schemas.homework import (
    DiyaGenerateHomeworkResponse,
    DiyaGeneratedHomeworkItem,
    HomeworkFeedItem,
    HomeworkFeedResponse,
    HomeworkResponse,
    HomeworkSubmissionListResponse,
    HomeworkSubmissionResponse,
)
from app.services.gemini_key_manager import generate_with_fallback

router = APIRouter()
NIL_UUID = UUID("00000000-0000-0000-0000-000000000000")

MAX_FILE_BYTES = 1 * 1024 * 1024  # 1MB
ALLOWED_MIME_EXACT = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_IMAGE_PREFIX = "image/"
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_DIYA_TEXT_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}
HOMEWORK_KIND_ASSIGNMENT = "assignment"
HOMEWORK_KIND_PRE_READING = "pre_reading"
HOMEWORK_KIND_RUNNING_NOTES = "running_notes"
HOMEWORK_KIND_SOLUTION = "solution"
HOMEWORK_KINDS = {
    HOMEWORK_KIND_ASSIGNMENT,
    HOMEWORK_KIND_PRE_READING,
    HOMEWORK_KIND_RUNNING_NOTES,
    HOMEWORK_KIND_SOLUTION,
}


def _file_ext(name: str) -> str:
    lower = (name or "").lower()
    idx = lower.rfind(".")
    return lower[idx:] if idx >= 0 else ""


async def _read_optional_file(file: Optional[UploadFile]) -> tuple[Optional[str], Optional[str], Optional[int], Optional[bytes]]:
    if file is None:
        return None, None, None, None
    file_name = file.filename or "attachment"
    ext = _file_ext(file_name)
    file_mime = file.content_type or ""
    if not (
        file_mime in ALLOWED_MIME_EXACT
        or file_mime.startswith(ALLOWED_IMAGE_PREFIX)
        or ext in ALLOWED_EXTENSIONS
    ):
        raise HTTPException(status_code=415, detail="Unsupported file. Allowed: PDF, DOCX, images.")
    file_bytes = await file.read()
    file_size = len(file_bytes or b"")
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if file_size > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Max size is 1 MB.")
    return file_name, file_mime, file_size, file_bytes


def _student_profile_for_user(user_id: UUID, db: Session) -> Optional[StudentProfile]:
    return db.query(StudentProfile).filter(StudentProfile.user_id == user_id).first()


def _teacher_profile_for_user(user_id: UUID, db: Session) -> Optional[TeacherProfile]:
    return db.query(TeacherProfile).filter(TeacherProfile.user_id == user_id).first()


def _can_student_access_class(student_user_id: UUID, class_id: UUID, db: Session) -> bool:
    sp = _student_profile_for_user(student_user_id, db)
    if not sp:
        return False
    cls = db.query(Class).filter(Class.id == class_id).first()
    if not cls:
        return False
    enrollment = db.query(Enrollment).filter(
        and_(
            Enrollment.student_id == sp.id,
            Enrollment.teacher_id == cls.teacher_id,
            Enrollment.is_active == True,
        )
    ).first()
    return enrollment is not None


def _normalize_kind(raw: Optional[str]) -> str:
    kind = str(raw or HOMEWORK_KIND_ASSIGNMENT).strip().lower()
    if kind not in HOMEWORK_KINDS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid homework kind. Allowed: "
                "assignment, pre_reading, running_notes, solution."
            ),
        )
    return kind


def _is_submittable_kind(kind: Optional[str]) -> bool:
    return str(kind or HOMEWORK_KIND_ASSIGNMENT).strip().lower() == HOMEWORK_KIND_ASSIGNMENT


def _can_student_access_homework(student_profile_id: UUID, hw: Homework) -> bool:
    if hw.target_student_id is None:
        return True
    return str(hw.target_student_id) == str(student_profile_id)


def _extract_text_from_uploaded_file(file_name: str, file_bytes: bytes) -> str:
    lower = (file_name or "").lower()
    if lower.endswith(".txt") or lower.endswith(".md"):
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("latin-1", errors="ignore")
    if lower.endswith(".docx"):
        try:
            from docx import Document

            doc = Document(BytesIO(file_bytes))
            return "\n".join(p.text.strip() for p in doc.paragraphs if p.text and p.text.strip())
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse DOCX file: {exc}") from exc
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(file_bytes))
            pages = []
            for page in reader.pages:
                pages.append((page.extract_text() or "").strip())
            return "\n".join(p for p in pages if p)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse PDF file: {exc}") from exc
    raise HTTPException(status_code=415, detail="Unsupported file type. Allowed: .txt, .md, .docx, .pdf")


async def _read_required_diya_context_file(file: Optional[UploadFile], field_label: str) -> tuple[str, str]:
    if file is None:
        raise HTTPException(status_code=422, detail=f"{field_label} file is required.")
    file_name = file.filename or "context"
    ext = _file_ext(file_name)
    if ext not in ALLOWED_DIYA_TEXT_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported {field_label} file type. Allowed: .txt, .md, .docx, .pdf")
    file_bytes = await file.read()
    size = len(file_bytes or b"")
    if size == 0:
        raise HTTPException(status_code=400, detail=f"Uploaded {field_label} file is empty.")
    if size > (4 * 1024 * 1024):
        raise HTTPException(status_code=413, detail=f"{field_label} file too large. Max size is 4 MB.")
    text = _extract_text_from_uploaded_file(file_name, file_bytes).strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"Could not extract text from {field_label} file.")
    return file_name, text


def _safe_str_list(items: Optional[list[str]]) -> str:
    values = [str(i).strip() for i in (items or []) if str(i).strip()]
    return ", ".join(values) if values else "Not available"


def _student_latest_profile_snapshot(student_id: UUID, db: Session) -> tuple[Optional[float], Optional[int]]:
    rows = db.query(StudentUnderstandingProfile).filter(
        StudentUnderstandingProfile.student_id == student_id
    ).all()
    latest_score: Optional[float] = None
    latest_level: Optional[int] = None
    latest_ts: Optional[datetime] = None
    for profile in rows:
        if profile.current_level is not None:
            latest_level = int(profile.current_level)
        for item in (profile.recent_scores or []):
            if not isinstance(item, dict):
                continue
            ts_raw = item.get("ts")
            score_raw = item.get("score")
            if score_raw is None:
                continue
            try:
                score = float(score_raw)
            except Exception:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else None
            except Exception:
                ts = None
            if latest_ts is None or (ts and ts > latest_ts):
                latest_ts = ts or latest_ts
                latest_score = score
    return latest_score, latest_level


def _extract_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty model response")
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("invalid json object")


def _generate_personalized_homework_content(
    *,
    class_title: str,
    subject: str,
    topic: str,
    student_name: str,
    grade: Optional[int],
    strengths: list[str],
    improvement_areas: list[str],
    performance_score: Optional[float],
    understanding_level: Optional[int],
    previous_year_questions_text: str,
    student_report_text: str,
) -> tuple[str, str]:
    prompt = f"""
You are Diya, creating personalized homework for an Indian school student.

Goal:
- Create ONE student-specific homework from the class topic.
- Adapt to strengths and weaknesses.
- Use previous year questions and student report context.
- Ensure homework differs meaningfully from other students.

Class context:
- Class title: {class_title}
- Subject: {subject}
- Topic: {topic}

Student context:
- Name: {student_name}
- Grade: {grade if grade is not None else "Unknown"}
- Strengths: {_safe_str_list(strengths)}
- Improvement areas: {_safe_str_list(improvement_areas)}
- Performance score: {performance_score if performance_score is not None else "Unknown"}
- Understanding level (1-5): {understanding_level if understanding_level is not None else "Unknown"}

Previous year questions context:
{previous_year_questions_text[:12000]}

Student report context:
{student_report_text[:12000]}

Return strict JSON only:
{{
  "title": "short homework title",
  "description": "Detailed homework instructions with 6-10 tasks. Include mixed difficulty and exam-style items. Mention why it fits this student."
}}
No markdown.
    """
    try:
        raw = generate_with_fallback(prompt, model_name="gemini-2.0-flash")
        parsed = _extract_json_object(raw or "")
        title = str(parsed.get("title") or "").strip()
        description = str(parsed.get("description") or "").strip()
        if not title or not description:
            raise ValueError("Missing title/description")
        return title, description
    except Exception:
        topic_clean = (topic or "Practice").strip()
        title = f"{topic_clean} Personalized Homework - {student_name}"
        strengths_text = _safe_str_list(strengths)
        weaknesses_text = _safe_str_list(improvement_areas)
        description = (
            f"Topic: {topic_clean}\n"
            f"Student profile focus: Strengths ({strengths_text}); Improvement areas ({weaknesses_text}).\n\n"
            "Tasks:\n"
            "1. Solve 3 easy warm-up questions from this topic.\n"
            "2. Solve 3 standard exam-style questions with full steps.\n"
            "3. Solve 2 higher-order application questions.\n"
            "4. Write short reflection: where you struggled and why.\n"
            "5. Re-attempt one question from mistakes using corrected method.\n\n"
            "This homework is individualized using student performance and report context."
        )
        return title, description


def _homework_to_response(
    hw: Homework,
    teacher_name: Optional[str],
    class_title: Optional[str],
    target_student_name: Optional[str] = None,
) -> HomeworkResponse:
    return HomeworkResponse(
        id=hw.id,
        class_id=hw.class_id,
        class_title=class_title or "Class",
        teacher_id=hw.teacher_id,
        teacher_name=teacher_name,
        target_student_id=hw.target_student_id,
        target_student_name=target_student_name,
        kind=hw.kind or HOMEWORK_KIND_ASSIGNMENT,
        generated_by_diya=bool(hw.generated_by_diya),
        title=hw.title,
        description=hw.description,
        due_at=hw.due_at,
        has_file=hw.file_bytes is not None,
        file_name=hw.file_name,
        file_size_bytes=hw.file_size_bytes,
        created_at=hw.created_at,
    )


def _submission_to_response(
    sub: HomeworkSubmission,
    hw: Homework,
    cls: Optional[Class],
    student_name: str,
    feedback_provider_name: Optional[str] = None,
) -> HomeworkSubmissionResponse:
    return HomeworkSubmissionResponse(
        id=sub.id,
        homework_id=sub.homework_id,
        class_id=hw.class_id,
        class_title=cls.title if cls else "Class",
        student_id=sub.student_id,
        student_name=student_name,
        submission_text=sub.submission_text,
        has_file=sub.file_bytes is not None,
        file_name=sub.file_name,
        file_size_bytes=sub.file_size_bytes,
        feedback_text=sub.feedback_text,
        feedback_score=sub.feedback_score,
        feedback_given_at=sub.feedback_given_at,
        feedback_provider_name=feedback_provider_name,
        submitted_at=sub.submitted_at,
    )


def _class_students_for_personalization(cls: Class, teacher_id: UUID, db: Session) -> list[tuple[StudentProfile, User]]:
    rows: list[tuple[StudentProfile, User]] = []
    if cls.batch_id:
        rows = db.query(StudentProfile, User).join(
            BatchMember, BatchMember.student_id == StudentProfile.id
        ).join(
            User, User.id == StudentProfile.user_id
        ).filter(
            BatchMember.batch_id == cls.batch_id
        ).all()
    else:
        rows = db.query(StudentProfile, User).join(
            Enrollment, Enrollment.student_id == StudentProfile.id
        ).join(
            User, User.id == StudentProfile.user_id
        ).filter(
            and_(
                Enrollment.teacher_id == teacher_id,
                Enrollment.is_active == True,
            )
        ).all()
    dedup: dict[str, tuple[StudentProfile, User]] = {}
    for sp, su in rows:
        dedup[str(sp.id)] = (sp, su)
    return list(dedup.values())


@router.post(
    "/classes/{class_id}",
    response_model=HomeworkResponse,
    summary="Create homework for a class (teacher only)",
)
async def create_homework(
    class_id: UUID,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    due_at: Optional[datetime] = Form(None),
    kind: Optional[str] = Form(HOMEWORK_KIND_ASSIGNMENT),
    target_student_id: Optional[UUID] = Form(None),
    generated_by_diya: Optional[bool] = Form(False),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher = _teacher_profile_for_user(current_user.id, db)
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    cls = db.query(Class).filter(and_(Class.id == class_id, Class.teacher_id == teacher.id)).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    clean_title = (title or "").strip()
    if not clean_title:
        raise HTTPException(status_code=422, detail="Homework title is required.")
    normalized_kind = _normalize_kind(kind)
    target_student: Optional[StudentProfile] = None
    target_student_user: Optional[User] = None
    if target_student_id is not None:
        target_student = db.query(StudentProfile).filter(StudentProfile.id == target_student_id).first()
        if not target_student:
            raise HTTPException(status_code=404, detail="Target student not found.")
        target_student_user = db.query(User).filter(User.id == target_student.user_id).first()
        if cls.batch_id:
            member = db.query(BatchMember).filter(
                and_(
                    BatchMember.batch_id == cls.batch_id,
                    BatchMember.student_id == target_student.id,
                )
            ).first()
            if not member:
                raise HTTPException(status_code=403, detail="Target student is not part of this class batch.")
        else:
            enrollment = db.query(Enrollment).filter(
                and_(
                    Enrollment.teacher_id == teacher.id,
                    Enrollment.student_id == target_student.id,
                    Enrollment.is_active == True,
                )
            ).first()
            if not enrollment:
                raise HTTPException(status_code=403, detail="Target student is not actively enrolled with this teacher.")

    file_name, file_mime, file_size_bytes, file_bytes = await _read_optional_file(file)

    hw = Homework(
        class_id=cls.id,
        teacher_id=teacher.id,
        target_student_id=target_student.id if target_student else None,
        kind=normalized_kind,
        generated_by_diya=bool(generated_by_diya),
        title=clean_title,
        description=(description or "").strip() or None,
        due_at=due_at if _is_submittable_kind(normalized_kind) else None,
        file_name=file_name,
        file_mime=file_mime,
        file_size_bytes=file_size_bytes,
        file_bytes=file_bytes,
    )
    db.add(hw)
    db.commit()
    db.refresh(hw)
    return _homework_to_response(
        hw,
        current_user.full_name,
        cls.title,
        target_student_name=target_student_user.full_name if target_student_user else None,
    )


@router.post(
    "/classes/{class_id}/generate-diya",
    response_model=DiyaGenerateHomeworkResponse,
    summary="Generate personalized homework with Diya for each student in class (teacher only)",
)
async def generate_diya_homework_for_class(
    class_id: UUID,
    topic: str = Form(...),
    due_at: Optional[datetime] = Form(None),
    previous_year_questions_file: UploadFile = File(...),
    student_report_file: UploadFile = File(...),
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher = _teacher_profile_for_user(current_user.id, db)
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    cls = db.query(Class).filter(and_(Class.id == class_id, Class.teacher_id == teacher.id)).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    topic_clean = (topic or "").strip()
    if not topic_clean:
        raise HTTPException(status_code=422, detail="Topic is required.")

    _, previous_year_text = await _read_required_diya_context_file(previous_year_questions_file, "previous year questions")
    _, report_text = await _read_required_diya_context_file(student_report_file, "student report")

    student_rows = _class_students_for_personalization(cls, teacher.id, db)
    if not student_rows:
        raise HTTPException(status_code=409, detail="No students found for this class.")

    created: list[DiyaGeneratedHomeworkItem] = []
    seen_titles: set[str] = set()
    for sp, su in student_rows:
        latest_score, latest_level = _student_latest_profile_snapshot(sp.id, db)
        title, description = _generate_personalized_homework_content(
            class_title=cls.title,
            subject=cls.subject,
            topic=topic_clean,
            student_name=su.full_name,
            grade=sp.grade,
            strengths=list(sp.strengths or []),
            improvement_areas=list(sp.improvement_areas or []),
            performance_score=latest_score if latest_score is not None else sp.performance_score,
            understanding_level=latest_level,
            previous_year_questions_text=previous_year_text,
            student_report_text=report_text,
        )

        if title.lower() in seen_titles:
            title = f"{title} - {su.full_name.split()[0]}"
        seen_titles.add(title.lower())

        hw = Homework(
            class_id=cls.id,
            teacher_id=teacher.id,
            target_student_id=sp.id,
            kind=HOMEWORK_KIND_ASSIGNMENT,
            generated_by_diya=True,
            title=title,
            description=description,
            due_at=due_at,
            file_name=None,
            file_mime=None,
            file_size_bytes=None,
            file_bytes=None,
        )
        db.add(hw)
        db.flush()

        db.add(
            Notification(
                user_id=su.id,
                notification_type="announcement",
                title=f"New personalized homework: {cls.title}",
                body=f"{current_user.full_name} assigned Diya-generated homework for topic '{topic_clean}'.",
                action_url="/dashboard.html#homework-section",
                extra_data={
                    "kind": "homework_personalized_diya",
                    "homework_id": str(hw.id),
                    "class_id": str(cls.id),
                },
            )
        )

        created.append(
            DiyaGeneratedHomeworkItem(
                homework_id=hw.id,
                student_id=sp.id,
                student_name=su.full_name,
                title=title,
            )
        )

    db.commit()
    return DiyaGenerateHomeworkResponse(
        class_id=cls.id,
        generated_count=len(created),
        items=created,
    )


@router.post(
    "/{homework_id}/submit",
    response_model=HomeworkSubmissionResponse,
    summary="Submit homework (student only)",
)
async def submit_homework(
    homework_id: UUID,
    submission_text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    sp = _student_profile_for_user(current_user.id, db)
    if not sp:
        raise HTTPException(status_code=404, detail="Student profile not found.")
    hw = db.query(Homework).filter(Homework.id == homework_id).first()
    if not hw:
        raise HTTPException(status_code=404, detail="Homework not found.")
    if not _can_student_access_class(current_user.id, hw.class_id, db):
        raise HTTPException(status_code=403, detail="You are not enrolled for this class.")
    if not _can_student_access_homework(sp.id, hw):
        raise HTTPException(status_code=403, detail="This homework is not assigned to your profile.")
    if not _is_submittable_kind(hw.kind):
        raise HTTPException(status_code=409, detail="Submissions are not required for this item.")

    text_value = (submission_text or "").strip() or None
    file_name, file_mime, file_size_bytes, file_bytes = await _read_optional_file(file)
    if not text_value and not file_bytes:
        raise HTTPException(status_code=422, detail="Provide submission text or upload a file.")

    sub = db.query(HomeworkSubmission).filter(
        and_(HomeworkSubmission.homework_id == homework_id, HomeworkSubmission.student_id == sp.id)
    ).first()
    if sub and sub.feedback_given_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Homework is already reviewed by teacher and cannot be resubmitted.",
        )
    if not sub:
        sub = HomeworkSubmission(
            homework_id=homework_id,
            student_id=sp.id,
        )
        db.add(sub)
    sub.submission_text = text_value
    if file_bytes is not None:
        sub.file_name = file_name
        sub.file_mime = file_mime
        sub.file_size_bytes = file_size_bytes
        sub.file_bytes = file_bytes
    sub.submitted_at = datetime.now(timezone.utc)
    sub.updated_at = datetime.now(timezone.utc)

    teacher_user = db.query(User).join(
        TeacherProfile, TeacherProfile.user_id == User.id
    ).filter(
        TeacherProfile.id == hw.teacher_id
    ).first()
    if teacher_user:
        db.add(
            Notification(
                user_id=teacher_user.id,
                notification_type="announcement",
                title=f"Homework submitted: {hw.title}",
                body=f"{current_user.full_name} submitted homework for class {hw.class_id}.",
                action_url="/teacher-dashboard.html#notifications-panel",
                extra_data={
                    "kind": "homework_submitted",
                    "homework_id": str(hw.id),
                    "submission_id": str(sub.id),
                    "class_id": str(hw.class_id),
                    "student_id": str(sp.id),
                },
            )
        )
    db.commit()
    db.refresh(sub)

    cls = db.query(Class).filter(Class.id == hw.class_id).first()
    return _submission_to_response(sub, hw, cls, current_user.full_name)


@router.patch(
    "/submissions/{submission_id}/feedback",
    response_model=HomeworkSubmissionResponse,
    summary="Provide homework feedback (teacher only)",
)
def provide_feedback(
    submission_id: UUID,
    feedback_text: Optional[str] = Form(None),
    feedback_score: Optional[int] = Form(None),
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher = _teacher_profile_for_user(current_user.id, db)
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    sub = db.query(HomeworkSubmission).filter(HomeworkSubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found.")
    hw = db.query(Homework).filter(Homework.id == sub.homework_id).first()
    if not hw:
        raise HTTPException(status_code=404, detail="Homework not found.")
    if hw.teacher_id != teacher.id:
        raise HTTPException(status_code=403, detail="Not allowed to feedback this submission.")

    text_value = (feedback_text or "").strip() or None
    if feedback_score is not None and (feedback_score < 0 or feedback_score > 100):
        raise HTTPException(status_code=422, detail="Feedback score must be between 0 and 100.")
    if text_value is None and feedback_score is None:
        raise HTTPException(status_code=422, detail="Provide feedback text or score.")

    sub.feedback_text = text_value
    sub.feedback_score = feedback_score
    sub.feedback_given_at = datetime.now(timezone.utc)
    sub.updated_at = datetime.now(timezone.utc)

    student_user = db.query(User).join(
        StudentProfile, StudentProfile.user_id == User.id
    ).filter(
        StudentProfile.id == sub.student_id
    ).first()
    if student_user:
        db.add(
            Notification(
                user_id=student_user.id,
                notification_type="announcement",
                title=f"Homework evaluated: {hw.title}",
                body=f"{current_user.full_name} evaluated your homework and shared feedback.",
                action_url="/dashboard.html#homework-section",
                extra_data={
                    "kind": "homework_feedback",
                    "homework_id": str(hw.id),
                    "submission_id": str(sub.id),
                    "class_id": str(hw.class_id),
                },
            )
        )
    db.commit()
    db.refresh(sub)

    cls = db.query(Class).filter(Class.id == hw.class_id).first()
    stu = db.query(User).join(StudentProfile, StudentProfile.user_id == User.id).filter(
        StudentProfile.id == sub.student_id
    ).first()
    return _submission_to_response(sub, hw, cls, stu.full_name if stu else "Student", current_user.full_name)


@router.get(
    "/me/teacher",
    response_model=list[HomeworkResponse],
    summary="List homework created by current teacher",
)
def list_teacher_homework(
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher = _teacher_profile_for_user(current_user.id, db)
    if not teacher:
        return []
    target_user = aliased(User)
    rows = db.query(Homework, Class, target_user).join(
        Class, Class.id == Homework.class_id
    ).outerjoin(
        StudentProfile, StudentProfile.id == Homework.target_student_id
    ).outerjoin(
        target_user, target_user.id == StudentProfile.user_id
    ).filter(
        Homework.teacher_id == teacher.id
    ).order_by(Homework.created_at.desc()).all()
    return [
        _homework_to_response(
            hw,
            current_user.full_name,
            cls.title if cls else None,
            target_student_name=target_u.full_name if target_u else None,
        )
        for hw, cls, target_u in rows
    ]


@router.get(
    "/me/teacher/submissions",
    response_model=HomeworkSubmissionListResponse,
    summary="List homework submissions for current teacher",
)
def list_teacher_submissions(
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher = _teacher_profile_for_user(current_user.id, db)
    if not teacher:
        return HomeworkSubmissionListResponse(submissions=[])

    rows = db.query(HomeworkSubmission, Homework, Class, StudentProfile, User).join(
        Homework, Homework.id == HomeworkSubmission.homework_id
    ).join(
        Class, Class.id == Homework.class_id
    ).join(
        StudentProfile, StudentProfile.id == HomeworkSubmission.student_id
    ).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        Homework.teacher_id == teacher.id
    ).order_by(HomeworkSubmission.submitted_at.desc()).all()

    submissions = [
        _submission_to_response(sub, hw, cls, user.full_name, current_user.full_name if sub.feedback_given_at else None)
        for sub, hw, cls, _sp, user in rows
    ]
    return HomeworkSubmissionListResponse(submissions=submissions)


@router.get(
    "/me/student-feed",
    response_model=HomeworkFeedResponse,
    summary="Student homework feed (homework + teacher-created assessments)",
)
def student_feed(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    sp = _student_profile_for_user(current_user.id, db)
    if not sp:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    active_teacher_ids = [tid for (tid,) in db.query(Enrollment.teacher_id).filter(
        and_(Enrollment.student_id == sp.id, Enrollment.is_active == True)
    ).all()]
    # Keep active teachers for current pending work, but also include homework
    # that this student already submitted in the past (historical feedback view).
    homework_map: dict[str, tuple[Homework, Class, User]] = {}
    if active_teacher_ids:
        active_homework_rows = db.query(Homework, Class, User).join(
            Class, Class.id == Homework.class_id
        ).join(
            TeacherProfile, TeacherProfile.id == Homework.teacher_id
        ).join(
            User, User.id == TeacherProfile.user_id
        ).filter(
            and_(
                Homework.teacher_id.in_(active_teacher_ids),
                or_(Homework.target_student_id.is_(None), Homework.target_student_id == sp.id),
            )
        ).all()
        for hw, cls, tu in active_homework_rows:
            homework_map[str(hw.id)] = (hw, cls, tu)

    submitted_homework_rows = db.query(HomeworkSubmission, Homework, Class, User).join(
        Homework, Homework.id == HomeworkSubmission.homework_id
    ).join(
        Class, Class.id == Homework.class_id
    ).join(
        TeacherProfile, TeacherProfile.id == Homework.teacher_id
    ).join(
        User, User.id == TeacherProfile.user_id
    ).filter(
        HomeworkSubmission.student_id == sp.id
    ).all()
    for _sub, hw, cls, tu in submitted_homework_rows:
        if not _can_student_access_homework(sp.id, hw):
            continue
        key = str(hw.id)
        if key not in homework_map:
            homework_map[key] = (hw, cls, tu)

    homework_rows = list(homework_map.values())

    submission_map = {
        str(s.homework_id): s
        for s in db.query(HomeworkSubmission).filter(HomeworkSubmission.student_id == sp.id).all()
    }

    assessment_rows = []
    if active_teacher_ids:
        assessment_rows = db.query(Class, User).join(
            TeacherProfile, TeacherProfile.id == Class.teacher_id
        ).join(
            User, User.id == TeacherProfile.user_id
        ).filter(
            and_(Class.teacher_id.in_(active_teacher_ids), Class.assessment_generated == True)
        ).all()

    assessed_by_class = {
        str(sa.class_id): sa
        for sa in db.query(StudentAssessment).filter(
            and_(StudentAssessment.student_id == sp.id, StudentAssessment.status == "evaluated")
        ).all()
    }

    items: list[HomeworkFeedItem] = []
    for hw, cls, tu in homework_rows:
        sub = submission_map.get(str(hw.id))
        is_submittable = _is_submittable_kind(hw.kind)
        items.append(
            HomeworkFeedItem(
                source_type="homework",
                id=str(hw.id),
                class_id=cls.id,
                class_title=cls.title,
                teacher_name=tu.full_name,
                target_student_id=hw.target_student_id,
                kind=hw.kind or HOMEWORK_KIND_ASSIGNMENT,
                generated_by_diya=bool(hw.generated_by_diya),
                is_submittable=is_submittable,
                title=hw.title,
                description=hw.description,
                due_at=hw.due_at,
                has_file=hw.file_bytes is not None,
                file_name=hw.file_name,
                file_size_bytes=hw.file_size_bytes,
                submission_id=str(sub.id) if sub else None,
                submission_status=(
                    "submitted" if sub else ("not_submitted" if is_submittable else "not_required")
                ),
                submitted_at=sub.submitted_at if sub else None,
                feedback_text=sub.feedback_text if sub else None,
                feedback_score=sub.feedback_score if sub else None,
                feedback_given_at=sub.feedback_given_at if sub else None,
                feedback_provider_name=tu.full_name if (sub and sub.feedback_given_at) else None,
                created_at=hw.created_at,
            )
        )

    for cls, tu in assessment_rows:
        result = assessed_by_class.get(str(cls.id))
        items.append(
            HomeworkFeedItem(
                source_type="assessment",
                id=f"assessment:{cls.id}",
                class_id=cls.id,
                class_title=cls.title,
                teacher_name=tu.full_name,
                title=f"Assessment: {cls.title}",
                description="Teacher has created an assessment for this class.",
                due_at=None,
                has_file=False,
                file_name=None,
                file_size_bytes=None,
                submission_id=None,
                submission_status="submitted" if result else "not_submitted",
                submitted_at=result.submitted_at if result else None,
                feedback_text=(
                    result.teacher_feedback_text
                    if result and result.teacher_feedback_text
                    else (f"Diya score: {round(result.percentage or 0, 1)}%" if result else None)
                ),
                feedback_score=(
                    result.teacher_feedback_score
                    if result and result.teacher_feedback_score is not None
                    else (int(round(result.percentage)) if result and result.percentage is not None else None)
                ),
                feedback_given_at=(
                    result.teacher_feedback_given_at
                    if result and result.teacher_feedback_given_at
                    else (result.submitted_at if result else None)
                ),
                feedback_provider_name=(
                    tu.full_name
                    if result and result.teacher_feedback_given_at
                    else ("Diya" if result else None)
                ),
                created_at=cls.updated_at or cls.created_at,
            )
        )

    # Include monthly/profile assessments taken with Diya.
    up_rows = db.query(StudentUnderstandingProfile).filter(
        StudentUnderstandingProfile.student_id == sp.id
    ).all()
    for up in up_rows:
        for rec in (up.recent_scores or []):
            if not isinstance(rec, dict) or rec.get("source") != "profile_assessment":
                continue
            ts_raw = rec.get("ts")
            try:
                taken_at = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else None
            except Exception:
                taken_at = None
            score = rec.get("score")
            grade = rec.get("grade")
            items.append(
                HomeworkFeedItem(
                    source_type="assessment",
                    id=f"profile-assessment:{ts_raw or datetime.now(timezone.utc).isoformat()}",
                    class_id=NIL_UUID,
                    class_title="Diya Profile Assessment",
                    teacher_name=None,
                    title="Monthly Profile Assessment",
                    description=(f"Grade {grade}" if grade is not None else "Adaptive profile assessment"),
                    due_at=None,
                    has_file=False,
                    file_name=None,
                    file_size_bytes=None,
                    submission_id=None,
                    submission_status="submitted",
                    submitted_at=taken_at,
                    feedback_text=(f"Diya score: {round(float(score), 1)}%" if score is not None else "Completed"),
                    feedback_score=(int(round(float(score))) if score is not None else None),
                    feedback_given_at=taken_at,
                    feedback_provider_name="Diya",
                    created_at=taken_at or datetime.now(timezone.utc),
                )
            )

    items.sort(key=lambda i: i.created_at, reverse=True)
    return HomeworkFeedResponse(items=items)


@router.get(
    "/{homework_id}/download",
    summary="Download homework attachment",
)
def download_homework_file(
    homework_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    hw = db.query(Homework).filter(Homework.id == homework_id).first()
    if not hw:
        raise HTTPException(status_code=404, detail="Homework not found.")
    if not hw.file_bytes:
        raise HTTPException(status_code=404, detail="No file attached to this homework.")

    allowed = False
    if current_user.role == "admin":
        allowed = True
    elif current_user.role == "teacher":
        tp = _teacher_profile_for_user(current_user.id, db)
        allowed = bool(tp and tp.id == hw.teacher_id)
    elif current_user.role == "student":
        sp = _student_profile_for_user(current_user.id, db)
        allowed = bool(
            sp
            and _can_student_access_class(current_user.id, hw.class_id, db)
            and _can_student_access_homework(sp.id, hw)
        )

    if not allowed:
        raise HTTPException(status_code=403, detail="You do not have access to this file.")

    filename = hw.file_name or "homework"
    return Response(
        content=hw.file_bytes,
        media_type=hw.file_mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/submissions/{submission_id}/download",
    summary="Download homework submission attachment",
)
def download_submission_file(
    submission_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    sub = db.query(HomeworkSubmission).filter(HomeworkSubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found.")
    if not sub.file_bytes:
        raise HTTPException(status_code=404, detail="No file attached to this submission.")
    hw = db.query(Homework).filter(Homework.id == sub.homework_id).first()
    if not hw:
        raise HTTPException(status_code=404, detail="Homework not found.")

    allowed = False
    if current_user.role == "admin":
        allowed = True
    elif current_user.role == "teacher":
        tp = _teacher_profile_for_user(current_user.id, db)
        allowed = bool(tp and tp.id == hw.teacher_id)
    elif current_user.role == "student":
        sp = _student_profile_for_user(current_user.id, db)
        allowed = bool(sp and sp.id == sub.student_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="You do not have access to this file.")

    filename = sub.file_name or "submission"
    return Response(
        content=sub.file_bytes,
        media_type=sub.file_mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
