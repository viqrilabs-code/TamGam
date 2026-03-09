# app/api/v1/endpoints/assessments.py
# Adaptive assessment endpoints
#
# Model reality:
#   - StudentAssessment owns both questions AND student answers (per student per class)
#   - Understanding level is integer 1-5 (not string)
#   - Questions have band: below | at_level | above
#   - Level recomputes every 3 classes as moving average

from datetime import datetime, timedelta, timezone
import logging
import base64
import hashlib
import hmac
import json
from io import BytesIO
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, File, UploadFile
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.config import settings
from app.core.dependencies import require_login, require_teacher
from app.db.session import get_db
from app.models.assessment import StudentAssessment, StudentUnderstandingProfile
from app.models.class_ import Class
from app.models.homework import Homework
from app.models.notification import Notification
from app.models.note import Note
from app.models.student import StudentProfile
from app.models.subscription import Subscription
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.schemas.assessment import (
    AnswerResult,
    AssessmentHistoryItem,
    AssessmentFeedbackRequest,
    MessageResponse,
    AssessmentResponse,
    AssessmentSubmitRequest,
    AssessmentSubmitResponse,
    AssessmentTeacherResponse,
    MCQOption,
    Question,
    QuestionForStudent,
    ProfileAssessmentGenerateResponse,
    ProfileAssessmentQuestion,
    ProfileAssessmentSubmitRequest,
    ProfileAssessmentSubmitResponse,
    TeacherAssessmentQuestion,
    TeacherGeneratedAssessmentResponse,
)
from app.services.gemini_key_manager import generate_with_fallback
from app.services.plan_limits import assert_feature_available, consume_feature

router = APIRouter()
logger = logging.getLogger("tamgam.assessments")

# Level labels for display
LEVEL_LABELS = {1: "beginner", 2: "developing", 3: "proficient", 4: "advanced", 5: "expert"}
LABEL_TO_INT = {v: k for k, v in LEVEL_LABELS.items()}

# Points per band
BAND_POINTS = {"below": 2, "at_level": 3, "above": 4}
PROFILE_BAND_POINTS = {"below": 1, "same": 1, "above": 1}
PROFILE_TOKEN_TTL_SECONDS = 30 * 60
PROFILE_RETAKE_DAYS = 30


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode())


def _sign_attempt_payload(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body_encoded = _b64url_encode(body)
    sig = hmac.new(settings.jwt_secret_key.encode(), body_encoded.encode(), hashlib.sha256).hexdigest()
    return f"{body_encoded}.{sig}"


def _verify_attempt_payload(token: str) -> dict:
    try:
        body_encoded, sig = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid attempt token.") from exc
    expected = hmac.new(settings.jwt_secret_key.encode(), body_encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=400, detail="Invalid attempt token signature.")
    try:
        payload = json.loads(_b64url_decode(body_encoded).decode())
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid attempt token payload.") from exc
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if int(payload.get("exp", 0)) < now_ts:
        raise HTTPException(status_code=400, detail="Assessment attempt expired. Please generate again.")
    return payload


def _normalize_mcq_options(raw_options: list) -> list:
    options = []
    for idx, opt in enumerate(raw_options[:4]):
        if isinstance(opt, dict):
            key = str(opt.get("key") or opt.get("label") or chr(65 + idx)).strip().upper()[:1]
            text = str(opt.get("text") or opt.get("value") or "").strip()
        else:
            key = chr(65 + idx)
            text = str(opt).strip()
        if text:
            options.append({"key": key, "text": text})
    while len(options) < 4:
        key = chr(65 + len(options))
        options.append({"key": key, "text": f"Option {key}"})
    return options


def _profile_fallback_questions(grade: int) -> List[dict]:
    return [
        {"id": 1, "difficulty_band": "below", "topic": "Number Sense", "question": f"For class {max(1, grade-1)}, what is 3/4 as a decimal?", "options": [{"key": "A", "text": "0.25"}, {"key": "B", "text": "0.5"}, {"key": "C", "text": "0.75"}, {"key": "D", "text": "1.25"}], "answer_key": "C"},
        {"id": 2, "difficulty_band": "below", "topic": "Algebra Basics", "question": "If x + 5 = 12, what is x?", "options": [{"key": "A", "text": "5"}, {"key": "B", "text": "7"}, {"key": "C", "text": "12"}, {"key": "D", "text": "17"}], "answer_key": "B"},
        {"id": 3, "difficulty_band": "same", "topic": "Fractions", "question": "Which fraction is equivalent to 6/8?", "options": [{"key": "A", "text": "2/3"}, {"key": "B", "text": "3/4"}, {"key": "C", "text": "4/5"}, {"key": "D", "text": "5/6"}], "answer_key": "B"},
        {"id": 4, "difficulty_band": "same", "topic": "Algebra", "question": "Solve: 2x - 4 = 10", "options": [{"key": "A", "text": "x = 3"}, {"key": "B", "text": "x = 6"}, {"key": "C", "text": "x = 7"}, {"key": "D", "text": "x = 8"}], "answer_key": "C"},
        {"id": 5, "difficulty_band": "same", "topic": "Geometry", "question": "How many degrees are there in a straight angle?", "options": [{"key": "A", "text": "90"}, {"key": "B", "text": "120"}, {"key": "C", "text": "180"}, {"key": "D", "text": "360"}], "answer_key": "C"},
        {"id": 6, "difficulty_band": "same", "topic": "Data Handling", "question": "The median of 3, 7, 9, 11, 15 is:", "options": [{"key": "A", "text": "7"}, {"key": "B", "text": "9"}, {"key": "C", "text": "11"}, {"key": "D", "text": "15"}], "answer_key": "B"},
        {"id": 7, "difficulty_band": "same", "topic": "Science Basics", "question": "Which process do plants use to make food?", "options": [{"key": "A", "text": "Respiration"}, {"key": "B", "text": "Photosynthesis"}, {"key": "C", "text": "Digestion"}, {"key": "D", "text": "Transpiration"}], "answer_key": "B"},
        {"id": 8, "difficulty_band": "same", "topic": "Mensuration", "question": "Area of rectangle with length 8 and breadth 5 is:", "options": [{"key": "A", "text": "13"}, {"key": "B", "text": "26"}, {"key": "C", "text": "40"}, {"key": "D", "text": "80"}], "answer_key": "C"},
        {"id": 9, "difficulty_band": "above", "topic": "Algebra Advanced", "question": "If 3x + 2 = 2x + 11, then x equals:", "options": [{"key": "A", "text": "7"}, {"key": "B", "text": "8"}, {"key": "C", "text": "9"}, {"key": "D", "text": "11"}], "answer_key": "C"},
        {"id": 10, "difficulty_band": "above", "topic": "Reasoning", "question": "A number is divisible by 3 if:", "options": [{"key": "A", "text": "Its last digit is even"}, {"key": "B", "text": "Sum of digits is divisible by 3"}, {"key": "C", "text": "It ends in 0"}, {"key": "D", "text": "It has 3 digits"}], "answer_key": "B"},
    ]


def _generate_profile_questions(student: StudentProfile) -> List[dict]:
    grade = int(student.grade or 8)
    strengths = ", ".join(student.strengths or []) or "none provided"
    improvements = ", ".join(student.improvement_areas or []) or "none provided"
    prompt = f"""
You are generating a student diagnostic assessment for Indian school curriculum.
Create exactly 10 MCQ questions in strict JSON.
Distribution rules:
- 2 questions at one standard below grade ({max(1, grade-1)})
- 6 questions at same grade ({grade})
- 2 questions at one standard above ({grade+1})

Student profile:
- Grade: {grade}
- Strengths: {strengths}
- Improvement areas: {improvements}

Return strict JSON as:
{{
  "questions": [
    {{
      "id": 1,
      "difficulty_band": "below|same|above",
      "topic": "short topic label",
      "question": "question text",
      "options": [{{"key":"A","text":"..."}},{{"key":"B","text":"..."}},{{"key":"C","text":"..."}},{{"key":"D","text":"..."}}],
      "answer_key": "A|B|C|D"
    }}
  ]
}}
No markdown, no explanation outside JSON.
"""
    try:
        raw = generate_with_fallback(prompt, model_name="gemini-2.0-flash")
        parsed = json.loads(raw)
        questions = parsed.get("questions", [])
        if not isinstance(questions, list) or len(questions) != 10:
            raise ValueError("Expected 10 questions.")
        normalized = []
        counts = {"below": 0, "same": 0, "above": 0}
        for idx, q in enumerate(questions, start=1):
            band = str(q.get("difficulty_band", "same")).strip().lower()
            if band not in counts:
                band = "same"
            counts[band] += 1
            options = _normalize_mcq_options(q.get("options") or [])
            answer_key = str(q.get("answer_key", "A")).strip().upper()[:1]
            if answer_key not in {"A", "B", "C", "D"}:
                answer_key = "A"
            normalized.append({
                "id": idx,
                "difficulty_band": band,
                "topic": str(q.get("topic") or "General").strip() or "General",
                "question": str(q.get("question") or "").strip() or f"Question {idx}",
                "options": options,
                "answer_key": answer_key,
            })
        if counts["below"] != 2 or counts["same"] != 6 or counts["above"] != 2:
            raise ValueError("Question distribution mismatch.")
        return normalized
    except Exception:
        return _profile_fallback_questions(grade)


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
    raise HTTPException(status_code=415, detail="Unsupported file type. Please upload .txt, .md, .docx, or .pdf.")


def _collect_pre_reading_text_for_class(class_id: UUID, teacher_id: UUID, db: Session) -> str:
    rows = db.query(Homework).filter(
        and_(
            Homework.class_id == class_id,
            Homework.teacher_id == teacher_id,
            Homework.kind == "pre_reading",
        )
    ).order_by(Homework.created_at.asc()).all()

    blocks: list[str] = []
    for idx, hw in enumerate(rows, start=1):
        parts: list[str] = []
        if hw.description:
            desc = str(hw.description).strip()
            if desc:
                parts.append(desc)
        if hw.file_bytes and hw.file_name:
            try:
                extracted = _extract_text_from_uploaded_file(hw.file_name, hw.file_bytes).strip()
                if extracted:
                    parts.append(extracted)
            except HTTPException:
                # Skip non-text extractable files (for example images) and
                # still keep title/description context if present.
                pass
        if not parts:
            continue
        title = (hw.title or f"Pre-reading #{idx}").strip()
        blocks.append(f"{title}\n" + "\n\n".join(parts))
    return "\n\n".join(blocks)


def _teacher_fallback_questions() -> List[dict]:
    questions = []
    for idx in range(1, 9):
        questions.append({
            "id": idx,
            "type": "mcq",
            "question": f"MCQ {idx}: Concept-check question based on class notes.",
            "options": [{"key": "A", "text": "Option A"}, {"key": "B", "text": "Option B"}, {"key": "C", "text": "Option C"}, {"key": "D", "text": "Option D"}],
            "answer": "A",
            "explanation": "Replace with class-specific reasoning.",
        })
    questions.append({"id": 9, "type": "subjective", "question": "Subjective Q1: Explain the main idea from class in your own words.", "answer": "Model answer based on class notes.", "explanation": "Assess conceptual clarity and examples."})
    questions.append({"id": 10, "type": "subjective", "question": "Subjective Q2: Apply the concept to a new situation/problem.", "answer": "Model answer with steps.", "explanation": "Assess transfer and reasoning."})
    return questions


def _generate_teacher_questions(class_title: str, subject: str, source_text: str) -> List[dict]:
    prompt = f"""
You are Diya, generating a class assessment.
Create exactly 10 questions from the provided class notes/transcript:
- 8 MCQ questions
- 2 subjective questions
Audience: Indian school students.
Class: {class_title}
Subject: {subject}

Source content:
{source_text[:12000]}

Return strict JSON:
{{
  "questions": [
    {{
      "id": 1,
      "type": "mcq|subjective",
      "question": "text",
      "options": [{{"key":"A","text":"..."}},{{"key":"B","text":"..."}},{{"key":"C","text":"..."}},{{"key":"D","text":"..."}}],
      "answer": "correct option key for mcq OR model answer for subjective",
      "explanation": "why this answer is correct / grading guidance"
    }}
  ]
}}
No markdown, only JSON.
"""
    try:
        raw = generate_with_fallback(prompt, model_name="gemini-2.0-flash")
        parsed = json.loads(raw)
        questions = parsed.get("questions", [])
        if not isinstance(questions, list) or len(questions) != 10:
            raise ValueError("Expected 10 questions.")
        normalized = []
        mcq_count = 0
        subjective_count = 0
        for idx, q in enumerate(questions, start=1):
            qtype = str(q.get("type", "mcq")).strip().lower()
            if qtype not in {"mcq", "subjective"}:
                qtype = "mcq"
            if qtype == "mcq":
                mcq_count += 1
            else:
                subjective_count += 1
            options = None
            if qtype == "mcq":
                options = _normalize_mcq_options(q.get("options") or [])
            normalized.append({
                "id": idx,
                "type": qtype,
                "question": str(q.get("question") or "").strip() or f"Question {idx}",
                "options": options,
                "answer": str(q.get("answer") or "").strip() or None,
                "explanation": str(q.get("explanation") or "").strip() or None,
            })
        if mcq_count != 8 or subjective_count != 2:
            raise ValueError("Expected exactly 8 MCQ and 2 subjective questions.")
        return normalized
    except Exception:
        return _teacher_fallback_questions()


def _latest_profile_assessment_ts(student_id: UUID, db: Session) -> Optional[datetime]:
    profiles = db.query(StudentUnderstandingProfile).filter(
        StudentUnderstandingProfile.student_id == student_id
    ).all()
    latest: Optional[datetime] = None
    for prof in profiles:
        history = list(prof.recent_scores or [])
        for item in history:
            if not isinstance(item, dict):
                continue
            if item.get("source") != "profile_assessment":
                continue
            ts_raw = item.get("ts")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if latest is None or ts > latest:
                latest = ts
    return latest


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _is_subscribed(user_id, db):
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.role == "student":
        return True
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _get_or_create_understanding(student_id, subject, db) -> StudentUnderstandingProfile:
    profile = db.query(StudentUnderstandingProfile).filter(
        and_(
            StudentUnderstandingProfile.student_id == student_id,
            StudentUnderstandingProfile.subject == subject,
        )
    ).first()
    if not profile:
        profile = StudentUnderstandingProfile(
            student_id=student_id,
            subject=subject,
            current_level=3,
            classes_since_last_recompute=0,
            total_classes_assessed=0,
        )
        db.add(profile)
        db.flush()
    return profile


def _recompute_level(profile: StudentUnderstandingProfile, percentage: float, class_id, db):
    """
    Update understanding level.
    Recomputes every 3 classes as moving average of recent scores.
    Returns (new_level_label, changed, old_level_label).
    """
    old_level = profile.current_level
    old_label = LEVEL_LABELS.get(old_level, "proficient")

    # Update recent_scores list
    recent = list(profile.recent_scores or [])
    recent.append({"class_id": str(class_id), "score": percentage})
    if len(recent) > 10:
        recent = recent[-10:]
    profile.recent_scores = recent

    profile.classes_since_last_recompute += 1
    profile.total_classes_assessed += 1
    profile.last_recomputed_at = datetime.now(timezone.utc)

    # Recompute every 3 classes
    if profile.classes_since_last_recompute >= 3:
        avg = sum(r["score"] for r in recent[-3:]) / 3
        profile.previous_level = old_level
        if avg >= 80 and old_level < 5:
            profile.current_level = old_level + 1
        elif avg < 40 and old_level > 1:
            profile.current_level = old_level - 1
        profile.classes_since_last_recompute = 0
    else:
        # Single-class nudge
        profile.previous_level = old_level
        if percentage >= 90 and old_level < 5:
            profile.current_level = old_level + 1
        elif percentage < 30 and old_level > 1:
            profile.current_level = old_level - 1

    new_label = LEVEL_LABELS.get(profile.current_level, "proficient")
    changed = profile.current_level != old_level
    return new_label, changed, old_label


def _mock_questions(subject: str, level: int) -> List[dict]:
    return [
        {
            "id": 1, "type": "mcq", "band": "below",
            "text": "What is a variable in algebra?",
            "options": ["A fixed number", "A letter representing an unknown", "An operator", "An equation"],
            "answer": "A letter representing an unknown",
            "explanation": "A variable like x or y represents an unknown number.",
            "hint": "Think about what changes in a formula.",
        },
        {
            "id": 2, "type": "mcq", "band": "at_level",
            "text": "What is the value of 2x + 3 when x = 4?",
            "options": ["9", "10", "11", "14"],
            "answer": "11",
            "explanation": "2(4)+3 = 8+3 = 11.",
            "hint": "Substitute x with 4.",
        },
        {
            "id": 3, "type": "mcq", "band": "at_level",
            "text": "Simplify: 3x + 2x + 5",
            "options": ["5x + 5", "6x + 5", "5x", "10x"],
            "answer": "5x + 5",
            "explanation": "3x + 2x = 5x, result is 5x + 5.",
            "hint": "Combine like terms first.",
        },
        {
            "id": 4, "type": "short_answer", "band": "above",
            "text": "If 3y - 7 = 8, what is the value of y?",
            "options": None,
            "answer": "5",
            "explanation": "3y = 15, y = 5.",
            "hint": "Isolate y by moving constants to the other side.",
        },
        {
            "id": 5, "type": "mcq", "band": "below",
            "text": "Which is an algebraic expression?",
            "options": ["42", "x + 3", "=", "None"],
            "answer": "x + 3",
            "explanation": "Algebraic expressions contain variables.",
            "hint": "Look for letters combined with numbers.",
        },
    ]


def _questions_for_student(questions: list) -> List[QuestionForStudent]:
    result = []
    for q in questions:
        opts = None
        if q.get("options"):
            opts = [MCQOption(key=str(i+1), text=o) if isinstance(o, str)
                    else MCQOption(**o) for i, o in enumerate(q["options"])]
        result.append(QuestionForStudent(
            id=q["id"], type=q["type"],
            level=LEVEL_LABELS.get(3, "proficient"),
            question=q.get("text", q.get("question", "")),
            options=opts,
        ))
    return result


def _questions_with_answers(questions: list) -> List[Question]:
    result = []
    for q in questions:
        opts = None
        if q.get("options"):
            opts = [MCQOption(key=str(i+1), text=o) if isinstance(o, str)
                    else MCQOption(**o) for i, o in enumerate(q["options"])]
        result.append(Question(
            id=q["id"], type=q["type"],
            level=q.get("band", "at_level"),
            question=q.get("text", q.get("question", "")),
            options=opts,
            correct_answer=q.get("answer"),
            explanation=q.get("explanation"),
        ))
    return result


def _run_generation(sa_id: UUID, note_content: dict, subject: str, level: int, db: Session):
    sa = db.query(StudentAssessment).filter(StudentAssessment.id == sa_id).first()
    if not sa:
        return
    sa.status = "in_progress"
    db.commit()
    try:
        questions = _mock_questions(subject, level)
        sa.questions = questions
        sa.total_questions = len(questions)
        sa.max_score = sum(BAND_POINTS.get(q.get("band", "at_level"), 3) for q in questions)
        sa.status = "pending"  # Ready for student
        sa.level_at_generation = level
    except Exception as e:
        sa.status = "pending"
        logger.exception("Assessment generation error: %s", e)
    sa.updated_at = datetime.now(timezone.utc) if hasattr(sa, 'updated_at') else None
    db.commit()


# â”€â”€ Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/profile/generate",
    response_model=ProfileAssessmentGenerateResponse,
    summary="Generate profile assessment for current student",
)
def generate_profile_assessment(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    if not _is_subscribed(current_user.id, db):
        raise HTTPException(status_code=403, detail={"message": "Active subscription required.", "redirect": "/plans.html"})
    assert_feature_available(current_user.id, "profile_assessment_attempts_monthly", db)

    student = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    last_taken_at = _latest_profile_assessment_ts(student.id, db)
    if last_taken_at is not None:
        next_allowed = last_taken_at + timedelta(days=PROFILE_RETAKE_DAYS)
        now = datetime.now(timezone.utc)
        if now < next_allowed:
            next_allowed_iso = next_allowed.astimezone(timezone.utc).isoformat()
            next_allowed_date = next_allowed.strftime("%d %b %Y")
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"You can retake this assessment after {next_allowed_date} (30 days from last attempt).",
                    "next_allowed_at": next_allowed_iso,
                },
            )

    questions = _generate_profile_questions(student)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    token_payload = {
        "uid": str(current_user.id),
        "exp": now_ts + PROFILE_TOKEN_TTL_SECONDS,
        "grade": int(student.grade or 8),
        "questions": [
            {
                "id": q["id"],
                "difficulty_band": q["difficulty_band"],
                "topic": q["topic"],
                "answer_key": q["answer_key"],
            }
            for q in questions
        ],
    }
    attempt_token = _sign_attempt_payload(token_payload)
    consume_feature(current_user.id, "profile_assessment_attempts_monthly", db)
    db.commit()

    return ProfileAssessmentGenerateResponse(
        attempt_token=attempt_token,
        total_questions=len(questions),
        questions=[
            ProfileAssessmentQuestion(
                id=q["id"],
                type="mcq",
                difficulty_band=q["difficulty_band"],
                topic=q["topic"],
                question=q["question"],
                options=[MCQOption(**opt) for opt in q["options"]],
            )
            for q in questions
        ],
    )


@router.post(
    "/profile/submit",
    response_model=ProfileAssessmentSubmitResponse,
    summary="Submit profile assessment and update adaptive profile",
)
def submit_profile_assessment(
    payload: ProfileAssessmentSubmitRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    token_data = _verify_attempt_payload(payload.attempt_token)
    if token_data.get("uid") != str(current_user.id):
        raise HTTPException(status_code=403, detail="This assessment attempt does not belong to you.")

    student = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    question_map = {int(q["id"]): q for q in (token_data.get("questions") or [])}
    if not question_map:
        raise HTTPException(status_code=400, detail="Assessment attempt has no questions.")

    answer_map = {int(a.question_id): str(a.answer_key).strip().upper()[:1] for a in payload.answers}
    correct = 0
    total = len(question_map)
    correct_topics = []
    incorrect_topics = []
    for qid, q in question_map.items():
        expected = str(q.get("answer_key") or "").strip().upper()[:1]
        actual = answer_map.get(qid, "")
        if actual == expected:
            correct += PROFILE_BAND_POINTS.get(str(q.get("difficulty_band", "same")), 1)
            correct_topics.append(str(q.get("topic") or "General"))
        else:
            incorrect_topics.append(str(q.get("topic") or "General"))

    score = round((correct / max(1, total)) * 100.0, 1)

    def _top_topics(items: List[str]) -> List[str]:
        rank = {}
        for it in items:
            key = (it or "General").strip()
            rank[key] = rank.get(key, 0) + 1
        return [k for k, _ in sorted(rank.items(), key=lambda kv: kv[1], reverse=True)[:3]]

    new_strengths = _top_topics(correct_topics)
    new_improvements = _top_topics(incorrect_topics)

    merged_strengths = []
    for item in (student.strengths or []) + new_strengths:
        if item not in merged_strengths:
            merged_strengths.append(item)
    merged_improvements = []
    for item in (student.improvement_areas or []) + new_improvements:
        if item not in merged_improvements:
            merged_improvements.append(item)

    student.strengths = merged_strengths[:5]
    student.improvement_areas = merged_improvements[:5]
    student.performance_score = score
    student.updated_at = datetime.now(timezone.utc)

    understanding = _get_or_create_understanding(student.id, "General", db)
    if score >= 85:
        understanding.current_level = 5
    elif score >= 70:
        understanding.current_level = 4
    elif score >= 50:
        understanding.current_level = 3
    elif score >= 30:
        understanding.current_level = 2
    else:
        understanding.current_level = 1

    history = list(understanding.recent_scores or [])
    history.append({
        "source": "profile_assessment",
        "score": score,
        "grade": int(token_data.get("grade") or student.grade or 8),
        "strengths": student.strengths,
        "improvement_areas": student.improvement_areas,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    understanding.recent_scores = history[-20:]
    understanding.last_recomputed_at = datetime.now(timezone.utc)
    understanding.updated_at = datetime.now(timezone.utc)

    db.commit()

    return ProfileAssessmentSubmitResponse(
        score=score,
        correct_count=int(round((score / 100.0) * total)),
        total_questions=total,
        grade_standard=int(token_data.get("grade") or student.grade or 8),
        strengths=student.strengths or [],
        improvement_areas=student.improvement_areas or [],
        understanding_level=int(understanding.current_level or 3),
    )


@router.post(
    "/{class_id}/generate-from-upload",
    response_model=TeacherGeneratedAssessmentResponse,
    summary="Generate class assessment from pre-reading and optional uploads (teacher only)",
)
async def generate_assessment_from_upload(
    class_id: UUID,
    file: Optional[UploadFile] = File(None),
    previous_year_questions_file: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    cls = db.query(Class).filter(and_(Class.id == class_id, Class.teacher_id == teacher.id)).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    source_parts: list[str] = []

    pre_reading_text = _collect_pre_reading_text_for_class(cls.id, teacher.id, db).strip()
    if pre_reading_text:
        source_parts.append(f"Pre-reading context:\n{pre_reading_text}")

    if file is not None:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded class context file is empty.")
        uploaded_text = _extract_text_from_uploaded_file(file.filename or "", file_bytes).strip()
        if not uploaded_text:
            raise HTTPException(status_code=400, detail="Could not extract text from uploaded class context file.")
        source_parts.append(f"Uploaded class context:\n{uploaded_text}")

    if previous_year_questions_file is not None:
        pyq_bytes = await previous_year_questions_file.read()
        if not pyq_bytes:
            raise HTTPException(status_code=400, detail="Uploaded previous year questions file is empty.")
        pyq_text = _extract_text_from_uploaded_file(previous_year_questions_file.filename or "", pyq_bytes).strip()
        if not pyq_text:
            raise HTTPException(status_code=400, detail="Could not extract text from previous year questions file.")
        source_parts.append(f"Previous year questions context:\n{pyq_text}")

    if not source_parts:
        raise HTTPException(
            status_code=422,
            detail=(
                "No assessment source content found. "
                "Upload pre-reading for this class, or upload class context/previous year questions in this popup."
            ),
        )

    source_text = "\n\n".join(source_parts)

    questions = _generate_teacher_questions(cls.title, cls.subject, source_text)
    mcq_count = sum(1 for q in questions if q["type"] == "mcq")
    subjective_count = sum(1 for q in questions if q["type"] == "subjective")

    # Persist/update a class template so students can open and submit this assessment.
    template_questions = []
    for q in questions:
        item = dict(q)
        item["band"] = str(item.get("band") or "at_level")
        if str(item.get("type") or "").lower() == "subjective":
            item["type"] = "short_answer"
        template_questions.append(item)

    max_score = sum(BAND_POINTS.get(str(q.get("band") or "at_level"), 3) for q in template_questions)
    template = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == None)
    ).first()
    if not template:
        template = StudentAssessment(
            class_id=class_id,
            student_id=None,
            status="pending",
            time_limit_seconds=600,
        )
        db.add(template)
    template.questions = template_questions
    template.student_answers = None
    template.total_questions = len(template_questions)
    template.score = None
    template.max_score = float(max_score or max(1, len(template_questions)))
    template.percentage = None
    template.below_correct = 0
    template.at_level_correct = 0
    template.above_correct = 0
    template.started_at = None
    template.submitted_at = None
    template.teacher_feedback_text = None
    template.teacher_feedback_score = None
    template.teacher_feedback_given_at = None
    template.level_at_generation = 3
    template.status = "pending"

    cls.assessment_generated = True
    cls.updated_at = datetime.now(timezone.utc)
    db.commit()

    return TeacherGeneratedAssessmentResponse(
        class_id=class_id,
        total_questions=len(questions),
        mcq_count=mcq_count,
        subjective_count=subjective_count,
        questions=[
            TeacherAssessmentQuestion(
                id=q["id"],
                type=q["type"],
                question=q["question"],
                options=[MCQOption(**opt) for opt in (q.get("options") or [])] if q.get("options") else None,
                answer=q.get("answer"),
                explanation=q.get("explanation"),
            )
            for q in questions
        ],
    )

@router.post(
    "/{class_id}/generate",
    response_model=AssessmentResponse,
    status_code=201,
    summary="Generate assessment for a class (teacher only)",
)
def generate_assessment(
    class_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher triggers assessment generation for a class.
    Creates one StudentAssessment template per class.
    Each student gets the same questions (personalised scoring via bands).
    Requires completed notes.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    note = db.query(Note).filter(
        and_(Note.class_id == class_id, Note.status == "completed")
    ).first()
    if not note:
        raise HTTPException(
            status_code=422,
            detail="No completed notes found. Generate and approve notes first.",
        )

    # Check if template assessment already exists
    existing = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == None)
    ).first()
    if existing and existing.status in ("pending", "in_progress"):
        raise HTTPException(status_code=409, detail=f"Assessment already exists (status: {existing.status}).")

    # Create template (student_id=None means it's the class template)
    sa = StudentAssessment(
        class_id=class_id,
        student_id=None,
        status="in_progress",
        time_limit_seconds=600,
    )
    db.add(sa)
    cls.assessment_generated = True
    db.commit()
    db.refresh(sa)

    background_tasks.add_task(_run_generation, sa.id, note.content or {}, cls.subject, 3, db)

    return AssessmentResponse(
        id=sa.id, class_id=class_id, status="in_progress",
        questions=None, total_questions=None, is_gated=False,
        created_at=sa.created_at, updated_at=sa.created_at,
    )


@router.get(
    "/{class_id}",
    summary="Get assessment for a class",
)
def get_assessment(
    class_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Get class assessment questions.
    Students see questions without answers (subscription required).
    Teachers see full questions with correct answers.
    """
    sa = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == None)
    ).first()
    if not sa:
        raise HTTPException(status_code=404, detail="No assessment found for this class.")

    questions = sa.questions or []

    if current_user.role in ("teacher", "admin"):
        return AssessmentTeacherResponse(
            id=sa.id, class_id=class_id, status=sa.status,
            questions=_questions_for_student(questions),
            questions_with_answers=_questions_with_answers(questions),
            total_questions=len(questions), is_gated=False,
            created_at=sa.created_at, updated_at=sa.created_at,
        )

    if not _is_subscribed(current_user.id, db):
        return AssessmentResponse(
            id=sa.id, class_id=class_id, status=sa.status,
            questions=None, total_questions=len(questions), is_gated=True,
            created_at=sa.created_at, updated_at=sa.created_at,
        )

    return AssessmentResponse(
        id=sa.id, class_id=class_id, status=sa.status,
        questions=_questions_for_student(questions),
        total_questions=len(questions), is_gated=False,
        created_at=sa.created_at, updated_at=sa.created_at,
    )


@router.post(
    "/{class_id}/submit",
    response_model=AssessmentSubmitResponse,
    summary="Submit assessment answers",
)
def submit_assessment(
    class_id: UUID,
    payload: AssessmentSubmitRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Student submits answers. Scores automatically.
    Updates understanding level. One attempt only.
    """
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    if not _is_subscribed(current_user.id, db):
        raise HTTPException(status_code=403, detail={"message": "Active subscription required.", "redirect": "/pricing"})
    assert_feature_available(current_user.id, "class_assessment_submissions_monthly", db)

    sp = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    # Get template
    template = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == None)
    ).first()
    if not template or template.status != "pending":
        raise HTTPException(status_code=404, detail="No ready assessment found for this class.")

    # Check not already submitted
    existing = db.query(StudentAssessment).filter(
        and_(StudentAssessment.class_id == class_id, StudentAssessment.student_id == sp.id)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="You have already submitted this assessment.")

    cls = db.query(Class).filter(Class.id == class_id).first()
    subject = cls.subject if cls else "General"
    questions = {q["id"]: q for q in (template.questions or [])}
    answer_map = {a.question_id: a.answer for a in payload.answers}

    results = []
    raw_score = 0.0
    max_score = template.max_score or 1
    bands = {"below": 0, "at_level": 0, "above": 0}

    for qid, q in questions.items():
        student_answer = answer_map.get(qid, "")
        correct = q.get("answer", "")
        is_correct = student_answer.strip().lower() == correct.strip().lower()
        band = q.get("band", "at_level")
        if is_correct:
            raw_score += BAND_POINTS.get(band, 3)
            bands[band] = bands.get(band, 0) + 1

        opts = None
        if q.get("options"):
            opts_list = q["options"]
            opts = [MCQOption(key=str(i+1), text=o) if isinstance(o, str) else MCQOption(**o)
                    for i, o in enumerate(opts_list)]

        results.append(AnswerResult(
            question_id=qid,
            question=q.get("text", q.get("question", "")),
            your_answer=student_answer,
            correct_answer=correct,
            is_correct=is_correct,
            explanation=q.get("explanation"),
        ))

    percentage = (raw_score / max_score * 100) if max_score > 0 else 0.0
    correct_count = sum(1 for r in results if r.is_correct)

    understanding = _get_or_create_understanding(sp.id, subject, db)
    new_level_label, level_changed, old_label = _recompute_level(understanding, percentage, class_id, db)

    # Create student's own record
    student_sa = StudentAssessment(
        class_id=class_id,
        student_id=sp.id,
        questions=template.questions,
        student_answers=answer_map,
        total_questions=len(questions),
        score=raw_score,
        max_score=max_score,
        percentage=round(percentage, 1),
        below_correct=bands.get("below", 0),
        at_level_correct=bands.get("at_level", 0),
        above_correct=bands.get("above", 0),
        status="evaluated",
        level_at_generation=understanding.current_level,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(student_sa)

    if cls:
        teacher_user = db.query(User).join(
            TeacherProfile, TeacherProfile.user_id == User.id
        ).filter(
            TeacherProfile.id == cls.teacher_id
        ).first()
        if teacher_user:
            db.add(
                Notification(
                    user_id=teacher_user.id,
                    notification_type="announcement",
                    title=f"Assessment submitted: {cls.title}",
                    body=f"{current_user.full_name} submitted the class assessment.",
                    action_url="/teacher-dashboard.html#my-classes",
                    extra_data={
                        "kind": "assessment_submitted",
                        "class_id": str(cls.id),
                        "assessment_id": str(student_sa.id),
                        "student_id": str(sp.id),
                    },
                )
            )
    consume_feature(current_user.id, "class_assessment_submissions_monthly", db)
    db.commit()

    return AssessmentSubmitResponse(
        assessment_id=student_sa.id,
        class_id=class_id,
        score=round(percentage, 1),
        correct_count=correct_count,
        total_questions=len(questions),
        results=results,
        understanding_level=new_level_label,
        level_changed=level_changed,
        previous_level=old_label if level_changed else None,
    )


@router.get(
    "/me/history",
    response_model=List[AssessmentHistoryItem],
    summary="Get own assessment history",
)
def get_assessment_history(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Student's assessment history across all classes."""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    sp = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    results = db.query(StudentAssessment, Class).join(
        Class, Class.id == StudentAssessment.class_id
    ).filter(
        and_(StudentAssessment.student_id == sp.id, StudentAssessment.status == "evaluated")
    ).order_by(StudentAssessment.submitted_at.desc()).all()

    return [
        AssessmentHistoryItem(
            id=sa.id,
            class_id=cls.id,
            class_title=cls.title,
            subject=cls.subject,
            score=sa.percentage or 0.0,
            correct_count=int(sa.below_correct + sa.at_level_correct + sa.above_correct),
            total_questions=sa.total_questions or 0,
            understanding_level=LEVEL_LABELS.get(sa.level_at_generation or 3, "proficient"),
            submitted_at=sa.submitted_at or sa.created_at,
            teacher_feedback_text=sa.teacher_feedback_text,
            teacher_feedback_score=sa.teacher_feedback_score,
            teacher_feedback_given_at=sa.teacher_feedback_given_at,
            feedback_provider_name=cls.teacher.user.full_name if cls and cls.teacher and cls.teacher.user else None,
        )
        for sa, cls in results
    ]


@router.patch(
    "/submissions/{assessment_id}/feedback",
    response_model=MessageResponse,
    summary="Provide feedback for a student's assessment (teacher only)",
)
def provide_assessment_feedback(
    assessment_id: UUID,
    payload: AssessmentFeedbackRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    sa = db.query(StudentAssessment).filter(
        and_(
            StudentAssessment.id == assessment_id,
            StudentAssessment.student_id.isnot(None),
        )
    ).first()
    if not sa:
        raise HTTPException(status_code=404, detail="Assessment submission not found.")

    cls = db.query(Class).filter(Class.id == sa.class_id).first()
    if not cls or cls.teacher_id != teacher.id:
        raise HTTPException(status_code=403, detail="Not allowed to feedback this assessment.")

    text_value = (payload.feedback_text or "").strip() or None
    score_value = payload.feedback_score
    if text_value is None and score_value is None:
        raise HTTPException(status_code=422, detail="Provide feedback text or score.")

    sa.teacher_feedback_text = text_value
    sa.teacher_feedback_score = score_value
    sa.teacher_feedback_given_at = datetime.now(timezone.utc)

    student_user = db.query(User).join(
        StudentProfile, StudentProfile.user_id == User.id
    ).filter(
        StudentProfile.id == sa.student_id
    ).first()
    if student_user:
        db.add(
            Notification(
                user_id=student_user.id,
                notification_type="announcement",
                title=f"Assessment feedback: {cls.title}",
                body=f"{current_user.full_name} shared assessment feedback.",
                action_url="/dashboard.html#homework-section",
                extra_data={
                    "kind": "assessment_feedback",
                    "class_id": str(cls.id),
                    "assessment_id": str(sa.id),
                },
            )
        )

    db.commit()
    return MessageResponse(message="Assessment feedback saved.")

