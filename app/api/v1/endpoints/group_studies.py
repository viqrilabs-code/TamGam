import json
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login
from app.db.session import get_db
from app.models.group_study import GroupStudy, GroupStudyParticipant, GroupStudyTurn
from app.models.notification import Notification
from app.models.student import Batch, BatchMember, StudentProfile
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.schemas.group_study import (
    GroupStudyAnswerRequest,
    GroupStudyCreatePayload,
    GroupStudyDetailResponse,
    GroupStudyListResponse,
    GroupStudyParticipantResponse,
    GroupStudyReportResponse,
    GroupStudyStudentSearchItem,
    GroupStudySubmitKeyRequest,
    GroupStudySummaryResponse,
    GroupStudyTurnOption,
    GroupStudyTurnResponse,
)
from app.services.gemini_key_manager import GeminiQuotaExhausted
from app.services.group_study_service import (
    build_group_study_report,
    decrypt_gemini_key,
    encrypt_gemini_key,
    evaluate_group_study_answer,
    generate_turn_payload,
    normalize_gemini_key,
    sectionize_content,
)

router = APIRouter()
logger = logging.getLogger("tamgam.group_study")

ALLOWED_DISCUSSION_TEXT_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}
MAX_DISCUSSION_FILE_BYTES = 4 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _file_ext(name: str) -> str:
    lower = (name or "").lower()
    idx = lower.rfind(".")
    return lower[idx:] if idx >= 0 else ""


def _teacher_profile_or_404(user_id: UUID, db: Session) -> TeacherProfile:
    profile = db.query(TeacherProfile).filter(TeacherProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    return profile


def _student_profile_or_404(user_id: UUID, db: Session) -> StudentProfile:
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Student profile not found.")
    return profile


async def _read_group_study_file(file: Optional[UploadFile]) -> tuple[Optional[str], Optional[str]]:
    if file is None:
        return None, None
    file_name = file.filename or "discussion"
    ext = _file_ext(file_name)
    if ext not in ALLOWED_DISCUSSION_TEXT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported discussion file type. Allowed: .txt, .md, .docx, .pdf")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded discussion file is empty.")
    if len(file_bytes) > MAX_DISCUSSION_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Discussion file too large. Max size is 4 MB.")
    if ext in {".txt", ".md"}:
        try:
            return file_name, file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_name, file_bytes.decode("latin-1", errors="ignore")
    if ext == ".docx":
        try:
            from docx import Document

            doc = Document(BytesIO(file_bytes))
            text = "\n".join(p.text.strip() for p in doc.paragraphs if p.text and p.text.strip())
            return file_name, text
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse DOCX discussion file: {exc}") from exc
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(file_bytes))
            text = "\n".join((page.extract_text() or "").strip() for page in reader.pages if page)
            return file_name, text
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse PDF discussion file: {exc}") from exc
    raise HTTPException(status_code=415, detail="Unsupported discussion file type.")


def _parse_uuid_list(raw_value: Optional[str]) -> list[UUID]:
    value = (raw_value or "").strip()
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="participant_user_ids must be valid JSON.") from exc
    if not isinstance(data, list):
        raise HTTPException(status_code=422, detail="participant_user_ids must be a JSON array.")
    result: list[UUID] = []
    for item in data:
        try:
            result.append(UUID(str(item)))
        except Exception as exc:
            raise HTTPException(status_code=422, detail="participant_user_ids contains an invalid UUID.") from exc
    return result


def _participant_row_for_user(study_id: UUID, user_id: UUID, db: Session) -> Optional[GroupStudyParticipant]:
    return db.query(GroupStudyParticipant).filter(
        and_(
            GroupStudyParticipant.group_study_id == study_id,
            GroupStudyParticipant.user_id == user_id,
        )
    ).first()


def _mark_participant_room_joined(study_id: UUID, current_user: User, db: Session) -> bool:
    participant = _participant_row_for_user(study_id, current_user.id, db)
    if not participant:
        return False
    changed = False
    if participant.joined_at is None:
        participant.joined_at = _now()
        changed = True
    if participant.status != "joined":
        participant.status = "joined"
        changed = True
    return changed


def _study_accessible_or_404(study_id: UUID, current_user: User, db: Session) -> GroupStudy:
    study = db.query(GroupStudy).filter(GroupStudy.id == study_id).first()
    if not study:
        raise HTTPException(status_code=404, detail="Group study not found.")
    if study.creator_user_id == current_user.id:
        return study
    participant = _participant_row_for_user(study_id, current_user.id, db)
    if participant:
        return study
    raise HTTPException(status_code=403, detail="You do not have access to this group study.")


def _participant_api_key(participant: GroupStudyParticipant) -> Optional[str]:
    encrypted = (participant.gemini_api_key_encrypted or "").strip()
    if not encrypted:
        return None
    try:
        key = decrypt_gemini_key(encrypted).strip()
    except (InvalidToken, ValueError):
        return None
    return key or None


def _participant_has_usable_api_key(participant: GroupStudyParticipant) -> bool:
    return _participant_api_key(participant) is not None


def _joined_key_holders(study_id: UUID, db: Session) -> list[dict]:
    rows = db.query(GroupStudyParticipant, User, StudentProfile).join(
        User, User.id == GroupStudyParticipant.user_id
    ).outerjoin(
        StudentProfile, StudentProfile.id == GroupStudyParticipant.student_id
    ).filter(
        and_(
            GroupStudyParticipant.group_study_id == study_id,
            GroupStudyParticipant.student_id.isnot(None),
            GroupStudyParticipant.gemini_api_key_encrypted.isnot(None),
        )
    ).all()
    result = []
    for participant, user, student in rows:
        api_key = _participant_api_key(participant)
        if not api_key:
            continue
        result.append(
            {
                "user_id": participant.user_id,
                "student_id": participant.student_id,
                "full_name": user.full_name,
                "avatar_url": user.avatar_url,
                "grade": student.grade if student else None,
                "participation_count": participant.participation_count,
                "correct_answers": participant.correct_answers,
                "total_score": participant.total_score,
                "api_key": api_key,
            }
        )
    return result


def _participant_rows(study_id: UUID, db: Session):
    return db.query(GroupStudyParticipant, User, StudentProfile).join(
        User, User.id == GroupStudyParticipant.user_id
    ).outerjoin(
        StudentProfile, StudentProfile.id == GroupStudyParticipant.student_id
    ).filter(
        GroupStudyParticipant.group_study_id == study_id
    ).order_by(GroupStudyParticipant.created_at.asc()).all()


def _coerce_section_entry(raw_section: object, *, index: int, fallback_title: str) -> Optional[dict]:
    if isinstance(raw_section, dict):
        text = str(raw_section.get("text") or raw_section.get("content") or raw_section.get("body") or "").strip()
        title = str(raw_section.get("title") or raw_section.get("heading") or fallback_title).strip() or fallback_title
        if not text:
            return None
        try:
            section_index = int(raw_section.get("index")) if raw_section.get("index") is not None else index
        except (TypeError, ValueError):
            section_index = index
        return {
            "index": section_index,
            "title": title,
            "text": text[:5000],
            "word_count": int(raw_section.get("word_count") or len(text.split())),
        }
    if isinstance(raw_section, str):
        text = raw_section.strip()
        if not text:
            return None
        return {
            "index": index,
            "title": fallback_title,
            "text": text[:5000],
            "word_count": len(text.split()),
        }
    return None


def _normalized_study_sections(study: GroupStudy) -> list[dict]:
    raw_sections = study.sections_payload
    if isinstance(raw_sections, dict):
        if isinstance(raw_sections.get("sections"), list):
            raw_sections = raw_sections.get("sections")
        else:
            raw_sections = [raw_sections]
    normalized: list[dict] = []
    if isinstance(raw_sections, list):
        for idx, raw_section in enumerate(raw_sections):
            item = _coerce_section_entry(raw_section, index=idx, fallback_title=f"Section {idx + 1}")
            if item:
                normalized.append(item)
    if normalized:
        return normalized
    fallback_source = (
        (study.document_text or "").strip()
        or (study.description or "").strip()
        or study.title
    )
    return sectionize_content(
        title=study.title,
        source_text=fallback_source,
        topic_outline=study.description,
    )


def _student_participants_ready(participant_rows: list[tuple[GroupStudyParticipant, User, Optional[StudentProfile]]]) -> bool:
    student_rows = [participant for participant, _, _ in participant_rows if participant.student_id is not None]
    return bool(student_rows) and all(
        participant.joined_at is not None and _participant_has_usable_api_key(participant)
        for participant in student_rows
    )


def _pending_group_study_turn(study_id: UUID, db: Session) -> Optional[GroupStudyTurn]:
    return db.query(GroupStudyTurn).filter(
        and_(
            GroupStudyTurn.group_study_id == study_id,
            GroupStudyTurn.status == "pending",
        )
    ).order_by(GroupStudyTurn.turn_index.desc()).first()


def _turn_time_limit_seconds(turn: GroupStudyTurn) -> Optional[int]:
    payload = turn.prompt_payload or {}
    try:
        value = int(payload.get("time_limit_seconds") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _turn_expires_at(turn: GroupStudyTurn) -> Optional[datetime]:
    time_limit_seconds = _turn_time_limit_seconds(turn)
    if not time_limit_seconds or not turn.created_at:
        return None
    return turn.created_at + timedelta(seconds=time_limit_seconds)


def _normalize_stop_approval_ids(raw_value: object) -> list[UUID]:
    result: list[UUID] = []
    seen: set[UUID] = set()
    for item in raw_value or []:
        try:
            parsed = UUID(str(item))
        except Exception:
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return result


def _required_stop_approver_ids(study: GroupStudy, participant_rows) -> list[UUID]:
    ordered: list[UUID] = []
    seen: set[UUID] = set()
    for user_id in [study.creator_user_id, *[participant.user_id for participant, _, _ in participant_rows]]:
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        ordered.append(user_id)
    return ordered


def _stop_request_state(study: GroupStudy, current_user: User, participant_rows, db: Session) -> dict:
    active = bool(study.stop_requested_at and study.stop_requester_user_id)
    if not active:
        return {
            "stop_request_active": False,
            "stop_request_reason": None,
            "stop_request_requested_by_name": None,
            "stop_request_approvals": 0,
            "stop_request_required": 0,
            "current_user_has_approved_stop": False,
            "stop_request_pending_names": [],
        }

    approvals = _normalize_stop_approval_ids(study.stop_approvals_payload)
    required_ids = _required_stop_approver_ids(study, participant_rows)
    name_map = {participant.user_id: user.full_name for participant, user, _ in participant_rows}
    if study.creator_user_id not in name_map:
        creator_user = db.query(User).filter(User.id == study.creator_user_id).first()
        if creator_user:
            name_map[creator_user.id] = creator_user.full_name

    requester_name = name_map.get(study.stop_requester_user_id) or (
        current_user.full_name if current_user.id == study.stop_requester_user_id else None
    )
    pending_names = [name_map.get(user_id, "Room member") for user_id in required_ids if user_id not in approvals]
    approval_count = len([user_id for user_id in required_ids if user_id in approvals])
    return {
        "stop_request_active": True,
        "stop_request_reason": study.stop_request_reason,
        "stop_request_requested_by_name": requester_name,
        "stop_request_approvals": approval_count,
        "stop_request_required": len(required_ids),
        "current_user_has_approved_stop": current_user.id in approvals,
        "stop_request_pending_names": pending_names,
    }


def _expire_pending_turn_if_needed(study: GroupStudy, db: Session, *, turn: Optional[GroupStudyTurn] = None) -> bool:
    pending_turn = turn
    if pending_turn is None:
        pending_turn = db.query(GroupStudyTurn).filter(
            and_(
                GroupStudyTurn.group_study_id == study.id,
                GroupStudyTurn.status == "pending",
            )
        ).order_by(GroupStudyTurn.turn_index.desc()).first()
    if not pending_turn or pending_turn.status != "pending":
        return False

    expires_at = _turn_expires_at(pending_turn)
    if not expires_at or _now() < expires_at:
        return False

    time_limit_seconds = _turn_time_limit_seconds(pending_turn) or 0
    if pending_turn.turn_type == "mcq_question":
        evaluation = {
            "feedback": f"Time limit expired after {time_limit_seconds} seconds.",
            "strengths": [],
            "improvement_areas": ["Answer the MCQ within the allotted time window."],
        }
        pending_turn.is_correct = False
    elif pending_turn.turn_type == "subjective_question":
        evaluation = {
            "feedback": f"Response window expired after {time_limit_seconds} seconds.",
            "strengths": [],
            "improvement_areas": ["Respond within the allowed time and include the main idea in your answer."],
        }
        pending_turn.is_correct = None
    else:
        evaluation = {
            "feedback": "Discussion window ended without a submitted response.",
            "strengths": [],
            "improvement_areas": ["Contribute before the discussion timer ends."],
        }
        pending_turn.is_correct = None

    pending_turn.evaluation_data = evaluation
    pending_turn.score_awarded = 0.0
    pending_turn.status = "answered"
    pending_turn.answered_at = expires_at

    if pending_turn.target_user_id:
        target_participant = _participant_row_for_user(study.id, pending_turn.target_user_id, db)
        if target_participant:
            if pending_turn.turn_type != "discussion_prompt":
                target_participant.total_questions = int(target_participant.total_questions or 0) + 1
            target_participant.updated_at = _now()

    db.flush()
    return True


def _turn_response(turn: GroupStudyTurn) -> GroupStudyTurnResponse:
    payload = turn.prompt_payload or {}
    return GroupStudyTurnResponse(
        id=turn.id,
        turn_index=turn.turn_index,
        section_index=turn.section_index,
        turn_type=turn.turn_type,
        section_title=turn.section_title,
        target_user_id=turn.target_user_id,
        target_name=turn.target_name,
        prompt_text=turn.prompt_text,
        question_text=turn.question_text,
        source_excerpt=turn.source_excerpt,
        difficulty_level=str(payload.get("difficulty_level") or "").strip() or None,
        time_limit_seconds=_turn_time_limit_seconds(turn),
        expires_at=_turn_expires_at(turn),
        options=[
            GroupStudyTurnOption(key=str(item.get("key") or ""), text=str(item.get("text") or ""))
            for item in (payload.get("options") or [])
            if isinstance(item, dict)
        ],
        answer_text=turn.answer_text,
        answer_choice=turn.answer_choice,
        evaluation_data=turn.evaluation_data,
        score_awarded=turn.score_awarded,
        is_correct=turn.is_correct,
        status=turn.status,
        created_at=turn.created_at,
        answered_at=turn.answered_at,
    )


def _report_response(report_payload: Optional[dict]) -> Optional[GroupStudyReportResponse]:
    if not report_payload:
        return None
    return GroupStudyReportResponse.model_validate(report_payload)


def _summary_response(
    study: GroupStudy,
    *,
    current_user: User,
    db: Session,
) -> GroupStudySummaryResponse:
    participants = db.query(GroupStudyParticipant.id).filter(GroupStudyParticipant.group_study_id == study.id).count()
    current_turn = db.query(GroupStudyTurn).filter(
        and_(
            GroupStudyTurn.group_study_id == study.id,
            GroupStudyTurn.status == "pending",
        )
    ).order_by(GroupStudyTurn.turn_index.desc()).first()
    batch_name = None
    if study.batch_id:
        batch = db.query(Batch).filter(Batch.id == study.batch_id).first()
        batch_name = batch.name if batch else None
    return GroupStudySummaryResponse(
        id=study.id,
        title=study.title,
        subject=study.subject,
        creator_role=study.creator_role,
        batch_id=study.batch_id,
        batch_name=batch_name,
        status=study.status,
        scheduled_at=study.scheduled_at,
        duration_minutes=study.duration_minutes,
        participant_count=participants,
        join_available=study.status in {"scheduled", "live"},
        current_turn_type=current_turn.turn_type if current_turn else None,
        current_target_name=current_turn.target_name if current_turn else None,
        created_at=study.created_at,
        started_at=study.started_at,
        ended_at=study.ended_at,
    )


def _detail_response(study: GroupStudy, current_user: User, db: Session) -> GroupStudyDetailResponse:
    summary = _summary_response(study, current_user=current_user, db=db)
    participant_rows = _participant_rows(study.id, db)
    current_participant = _participant_row_for_user(study.id, current_user.id, db)
    turns = db.query(GroupStudyTurn).filter(GroupStudyTurn.group_study_id == study.id).order_by(GroupStudyTurn.turn_index.asc()).all()
    pending_turn = next((turn for turn in turns if turn.status == "pending"), None)
    can_advance = bool(study.creator_user_id == current_user.id and study.status in {"scheduled", "live"} and pending_turn is None)
    can_start = bool(study.creator_user_id == current_user.id and study.status == "scheduled")
    stop_request_state = _stop_request_state(study, current_user, participant_rows, db)
    participants = [
        GroupStudyParticipantResponse(
            user_id=participant.user_id,
            student_id=participant.student_id,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            grade=student.grade if student else None,
            role=participant.role,
            invite_source=participant.invite_source,
            status=participant.status,
            has_submitted_api_key=_participant_has_usable_api_key(participant),
            joined_at=participant.joined_at,
            total_score=participant.total_score or 0.0,
            total_questions=participant.total_questions or 0,
            correct_answers=participant.correct_answers or 0,
            participation_count=participant.participation_count or 0,
        )
        for participant, user, student in participant_rows
    ]
    return GroupStudyDetailResponse(
        **summary.model_dump(),
        creator_user_id=study.creator_user_id,
        description=study.description,
        document_name=study.document_name,
        group_discussion_enabled=study.group_discussion_enabled,
        stop_reason=study.stop_reason,
        stop_request_active=stop_request_state["stop_request_active"],
        stop_request_reason=stop_request_state["stop_request_reason"],
        stop_request_requested_by_name=stop_request_state["stop_request_requested_by_name"],
        stop_request_approvals=stop_request_state["stop_request_approvals"],
        stop_request_required=stop_request_state["stop_request_required"],
        current_user_has_approved_stop=stop_request_state["current_user_has_approved_stop"],
        stop_request_pending_names=stop_request_state["stop_request_pending_names"],
        is_creator=study.creator_user_id == current_user.id,
        current_user_is_participant=current_participant is not None,
        current_user_has_api_key=bool(current_participant and _participant_has_usable_api_key(current_participant)),
        can_start=can_start,
        can_advance=can_advance,
        can_stop=bool((study.creator_user_id == current_user.id or current_participant is not None) and study.status in {"scheduled", "live"}),
        current_turn=_turn_response(pending_turn) if pending_turn else None,
        history=[_turn_response(turn) for turn in turns[-12:]],
        participants=participants,
        report=_report_response(study.report_payload),
    )


def _notify_participants(study: GroupStudy, participant_user_ids: list[UUID], current_user: User, db: Session) -> None:
    action_url = f"/group-study.html?study_id={study.id}&room_id={study.id}#study_id={study.id}"
    schedule_label = study.scheduled_at.strftime("%d %b %Y %I:%M %p UTC")
    for user_id in participant_user_ids:
        if user_id == current_user.id:
            continue
        db.add(
            Notification(
                user_id=user_id,
                notification_type="announcement",
                title=f"Upcoming group study: {study.title}",
                body=f"{current_user.full_name} scheduled a group study for {schedule_label}.",
                action_url=action_url,
                extra_data={
                    "kind": "group_study_scheduled",
                    "study_id": str(study.id),
                    "scheduled_at": study.scheduled_at.isoformat(),
                },
                is_read=False,
            )
        )


def _finalize_study(study: GroupStudy, *, status: str, reason: Optional[str], db: Session) -> None:
    participant_rows = _participant_rows(study.id, db)
    report = build_group_study_report(
        title=study.title,
        participants=[
            {
                "user_id": participant.user_id,
                "full_name": user.full_name,
                "total_score": participant.total_score or 0.0,
                "total_questions": participant.total_questions or 0,
                "correct_answers": participant.correct_answers or 0,
                "participation_count": participant.participation_count or 0,
            }
            for participant, user, _ in participant_rows
            if participant.student_id is not None
        ],
        turns=[
            {
                "target_user_id": turn.target_user_id,
                "evaluation_data": turn.evaluation_data or {},
            }
            for turn in db.query(GroupStudyTurn).filter(GroupStudyTurn.group_study_id == study.id).all()
        ],
    )
    study.status = status
    study.stop_reason = reason
    study.stop_request_reason = None
    study.stop_requester_user_id = None
    study.stop_requested_at = None
    study.stop_approvals_payload = []
    study.ended_at = _now()
    study.report_payload = report
    winner_id = report.get("winner_user_id")
    study.winner_user_id = UUID(str(winner_id)) if winner_id else None
    db.query(GroupStudyParticipant).filter(
        GroupStudyParticipant.group_study_id == study.id
    ).update(
        {"gemini_api_key_encrypted": None},
        synchronize_session=False,
    )


def _maybe_auto_start_study(study: GroupStudy, db: Session) -> bool:
    if study.status != "scheduled":
        return False
    participant_rows = _participant_rows(study.id, db)
    if not _student_participants_ready(participant_rows):
        return False
    pending_turn = _pending_group_study_turn(study.id, db)
    if pending_turn and _expire_pending_turn_if_needed(study, db, turn=pending_turn):
        pending_turn = None
    if pending_turn:
        return False
    try:
        _create_next_turn_or_finish(study, db)
    except HTTPException as exc:
        if exc.status_code == 409:
            return False
        raise
    return True


def _create_next_turn_or_finish(study: GroupStudy, db: Session) -> None:
    existing_pending = _pending_group_study_turn(study.id, db)
    if existing_pending and _expire_pending_turn_if_needed(study, db, turn=existing_pending):
        existing_pending = None
    if existing_pending:
        raise HTTPException(status_code=409, detail="Current group study turn is still awaiting an answer.")

    participant_rows = _participant_rows(study.id, db)
    missing_join_names = [
        user.full_name
        for participant, user, _ in participant_rows
        if participant.student_id is not None and participant.joined_at is None
    ]
    if missing_join_names:
        sample = ", ".join(missing_join_names[:5])
        if len(missing_join_names) > 5:
            sample = f"{sample}, and {len(missing_join_names) - 5} more"
        raise HTTPException(
            status_code=409,
            detail=f"Every invited student must open the room before the group study timer can start. Pending: {sample}.",
        )

    missing_key_names = [
        user.full_name
        for participant, user, _ in participant_rows
        if participant.student_id is not None and not _participant_has_usable_api_key(participant)
    ]
    if missing_key_names:
        sample = ", ".join(missing_key_names[:5])
        if len(missing_key_names) > 5:
            sample = f"{sample}, and {len(missing_key_names) - 5} more"
        raise HTTPException(
            status_code=409,
            detail=f"Every student in the room must submit a Gemini API key before Diya can proceed. Pending: {sample}.",
        )

    key_holders = _joined_key_holders(study.id, db)
    if not key_holders:
        raise HTTPException(status_code=409, detail="At least one student must submit a Gemini API key before Diya can proceed.")

    turns = db.query(GroupStudyTurn).filter(GroupStudyTurn.group_study_id == study.id).order_by(GroupStudyTurn.turn_index.asc()).all()
    question_turns = [turn for turn in turns if turn.turn_type != "discussion_prompt"]
    sections = _normalized_study_sections(study)
    if sections != list(study.sections_payload or []):
        study.sections_payload = sections
    next_turn_index = len(turns)
    discussion_exists = any(turn.turn_type == "discussion_prompt" for turn in turns)

    if len(question_turns) < len(sections):
        section = sections[len(question_turns)]
        try:
            payload = generate_turn_payload(
                study_id=str(study.id),
                turn_index=next_turn_index,
                subject=study.subject,
                section=section,
                participants=key_holders,
                api_keys=[item["api_key"] for item in key_holders],
                discussion_prompt=False,
            )
        except GeminiQuotaExhausted:
            _finalize_study(
                study,
                status="stopped",
                reason="All submitted Gemini API keys were exhausted.",
                db=db,
            )
            return
        db.add(
            GroupStudyTurn(
                group_study_id=study.id,
                target_user_id=payload.get("target_user_id"),
                turn_index=next_turn_index,
                section_index=int(section.get("index") or len(question_turns)),
                turn_type=str(payload.get("turn_type") or "subjective_question"),
                section_title=payload.get("section_title"),
                target_name=payload.get("target_name"),
                prompt_text=str(payload.get("prompt_text") or ""),
                question_text=payload.get("question_text"),
                source_excerpt=payload.get("source_excerpt"),
                prompt_payload={
                    "options": payload.get("options") or [],
                    "expected_points": payload.get("expected_points") or [],
                    "difficulty_level": payload.get("difficulty_level"),
                    "time_limit_seconds": payload.get("time_limit_seconds"),
                },
                correct_answer=payload.get("correct_answer"),
                status="pending",
            )
        )
        study.status = "live"
        if study.started_at is None:
            study.started_at = _now()
        return

    if study.group_discussion_enabled and not discussion_exists:
        section = sections[-1] if sections else {"index": 0, "title": study.title, "text": study.description or study.title}
        try:
            payload = generate_turn_payload(
                study_id=str(study.id),
                turn_index=next_turn_index,
                subject=study.subject,
                section=section,
                participants=key_holders,
                api_keys=[item["api_key"] for item in key_holders],
                discussion_prompt=True,
            )
        except GeminiQuotaExhausted:
            _finalize_study(
                study,
                status="stopped",
                reason="All submitted Gemini API keys were exhausted.",
                db=db,
            )
            return
        db.add(
            GroupStudyTurn(
                group_study_id=study.id,
                target_user_id=None,
                turn_index=next_turn_index,
                section_index=int(section.get("index") or 0),
                turn_type="discussion_prompt",
                section_title=payload.get("section_title"),
                target_name=None,
                prompt_text=str(payload.get("prompt_text") or ""),
                question_text=payload.get("question_text"),
                source_excerpt=payload.get("source_excerpt"),
                prompt_payload={
                    "options": [],
                    "expected_points": payload.get("expected_points") or [],
                    "difficulty_level": payload.get("difficulty_level"),
                    "time_limit_seconds": payload.get("time_limit_seconds"),
                },
                correct_answer=None,
                status="pending",
            )
        )
        study.status = "live"
        if study.started_at is None:
            study.started_at = _now()
        return

    _finalize_study(study, status="completed", reason="Group study completed.", db=db)


@router.get(
    "/search/students",
    response_model=list[GroupStudyStudentSearchItem],
    summary="Search student participants for group study",
)
def search_group_study_students(
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role not in {"teacher", "student"}:
        raise HTTPException(status_code=403, detail="Only teachers or students can search group study participants.")
    query = db.query(StudentProfile, User).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        and_(
            User.role == "student",
            User.is_active == True,
            User.id != current_user.id,
        )
    )
    search = (q or "").strip()
    if search:
        query = query.filter(User.full_name.ilike(f"%{search}%"))
    if current_user.role == "student":
        current_student = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
        if current_student and current_student.grade is not None:
            query = query.order_by((StudentProfile.grade == current_student.grade).desc(), User.full_name.asc())
        else:
            query = query.order_by(User.full_name.asc())
    else:
        query = query.order_by(User.full_name.asc())
    rows = query.limit(limit).all()
    return [
        GroupStudyStudentSearchItem(
            user_id=user.id,
            student_id=student.id,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            grade=student.grade,
            school_name=student.school_name,
        )
        for student, user in rows
    ]


@router.get(
    "/upcoming",
    response_model=GroupStudyListResponse,
    summary="List upcoming group studies for the current user",
)
def list_upcoming_group_studies(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    now = _now()
    query = db.query(GroupStudy)
    if current_user.role == "teacher":
        query = query.filter(GroupStudy.creator_user_id == current_user.id)
    else:
        query = query.join(
            GroupStudyParticipant, GroupStudyParticipant.group_study_id == GroupStudy.id
        ).filter(GroupStudyParticipant.user_id == current_user.id)
    rows = query.filter(
        or_(
            GroupStudy.status.in_(["scheduled", "live"]),
            and_(GroupStudy.status == "completed", GroupStudy.ended_at >= now - timedelta(hours=2)),
        )
    ).order_by(GroupStudy.scheduled_at.asc()).all()
    studies = [_summary_response(study, current_user=current_user, db=db) for study in rows]
    return GroupStudyListResponse(studies=studies, total=len(studies))


@router.get(
    "/mine",
    response_model=GroupStudyListResponse,
    summary="List group studies visible to the current user",
)
def list_my_group_studies(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    query = db.query(GroupStudy)
    if current_user.role == "teacher":
        query = query.filter(GroupStudy.creator_user_id == current_user.id)
    else:
        query = query.outerjoin(
            GroupStudyParticipant, GroupStudyParticipant.group_study_id == GroupStudy.id
        ).filter(
            or_(
                GroupStudy.creator_user_id == current_user.id,
                GroupStudyParticipant.user_id == current_user.id,
            )
        )
    rows = query.order_by(GroupStudy.created_at.desc()).all()
    studies = [_summary_response(study, current_user=current_user, db=db) for study in rows]
    return GroupStudyListResponse(studies=studies, total=len(studies))


@router.post(
    "/",
    response_model=GroupStudyDetailResponse,
    status_code=201,
    summary="Create a group study",
)
async def create_group_study(
    title: str = Form(...),
    subject: str = Form(...),
    scheduled_at: datetime = Form(...),
    duration_minutes: int = Form(60),
    batch_id: Optional[UUID] = Form(None),
    participant_user_ids: Optional[str] = Form(None),
    group_discussion_enabled: bool = Form(False),
    topic_outline: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role not in {"teacher", "student"}:
        raise HTTPException(status_code=403, detail="Only teachers or students can create group studies.")

    file_name, document_text = await _read_group_study_file(file)
    payload = GroupStudyCreatePayload(
        title=title,
        subject=subject,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        batch_id=batch_id,
        participant_user_ids=_parse_uuid_list(participant_user_ids),
        group_discussion_enabled=group_discussion_enabled,
        topic_outline=topic_outline,
        document_name=file_name,
        document_text=document_text,
    )

    participant_specs: list[dict] = []
    teacher_profile = None
    batch = None
    if current_user.role == "teacher":
        teacher_profile = _teacher_profile_or_404(current_user.id, db)
        if not payload.batch_id:
            raise HTTPException(status_code=422, detail="Teachers must create group study from an existing batch.")
        batch = db.query(Batch).join(
            TeacherProfile, TeacherProfile.id == Batch.teacher_id
        ).filter(
            and_(
                Batch.id == payload.batch_id,
                TeacherProfile.user_id == current_user.id,
            )
        ).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found.")
        if not batch.is_active:
            raise HTTPException(status_code=409, detail="Batch is inactive.")
        batch_members = db.query(BatchMember, StudentProfile, User).join(
            StudentProfile, StudentProfile.id == BatchMember.student_id
        ).join(
            User, User.id == StudentProfile.user_id
        ).filter(BatchMember.batch_id == batch.id).all()
        if not batch_members:
            raise HTTPException(status_code=409, detail="This batch has no students to invite into group study.")
        participant_specs = [
            {
                "user_id": user.id,
                "student_id": student.id,
                "role": "participant",
                "invite_source": "batch",
            }
            for _, student, user in batch_members
        ]
    else:
        creator_student = _student_profile_or_404(current_user.id, db)
        user_ids = {current_user.id, *payload.participant_user_ids}
        if payload.batch_id:
            batch = db.query(Batch).join(
                BatchMember, BatchMember.batch_id == Batch.id
            ).filter(
                and_(
                    Batch.id == payload.batch_id,
                    BatchMember.student_id == creator_student.id,
                )
            ).first()
            if not batch:
                raise HTTPException(status_code=404, detail="You do not belong to this batch.")
            batch_rows = db.query(BatchMember, StudentProfile, User).join(
                StudentProfile, StudentProfile.id == BatchMember.student_id
            ).join(
                User, User.id == StudentProfile.user_id
            ).filter(BatchMember.batch_id == batch.id).all()
            for _, student, user in batch_rows:
                user_ids.add(user.id)
        student_rows = db.query(StudentProfile, User).join(
            User, User.id == StudentProfile.user_id
        ).filter(
            and_(
                User.id.in_(list(user_ids)),
                User.role == "student",
                User.is_active == True,
            )
        ).all()
        found_user_ids = {user.id for _, user in student_rows}
        missing = [str(user_id) for user_id in user_ids if user_id not in found_user_ids]
        if missing:
            raise HTTPException(status_code=404, detail="One or more invited students could not be found.")
        participant_specs = [
            {
                "user_id": user.id,
                "student_id": student.id,
                "role": "host" if user.id == current_user.id else "participant",
                "invite_source": "batch" if batch and user.id != current_user.id else ("host" if user.id == current_user.id else "search"),
            }
            for student, user in student_rows
        ]

    sections = sectionize_content(
        title=payload.title,
        source_text=payload.document_text or payload.topic_outline or "",
        topic_outline=payload.topic_outline,
    )
    study = GroupStudy(
        creator_user_id=current_user.id,
        creator_role=current_user.role,
        teacher_id=teacher_profile.id if teacher_profile else (batch.teacher_id if batch else None),
        batch_id=batch.id if batch else None,
        title=payload.title,
        subject=payload.subject,
        description=payload.topic_outline,
        document_name=payload.document_name,
        document_text=(payload.document_text or payload.topic_outline or "")[:50000],
        sections_payload=sections,
        group_discussion_enabled=payload.group_discussion_enabled,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        status="scheduled",
    )
    db.add(study)
    db.flush()

    participant_user_ids_to_notify: list[UUID] = []
    for spec in participant_specs:
        participant = GroupStudyParticipant(
            group_study_id=study.id,
            user_id=spec["user_id"],
            student_id=spec["student_id"],
            invited_by_user_id=current_user.id,
            role=spec["role"],
            invite_source=spec["invite_source"],
            status="joined" if spec["role"] == "host" else "invited",
            joined_at=_now() if spec["role"] == "host" else None,
        )
        db.add(participant)
        participant_user_ids_to_notify.append(spec["user_id"])

    _notify_participants(study, participant_user_ids_to_notify, current_user, db)
    db.commit()
    db.refresh(study)
    return _detail_response(study, current_user, db)


@router.get(
    "/{study_id}",
    response_model=GroupStudyDetailResponse,
    summary="Get group study detail",
)
def get_group_study(
    study_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    study = _study_accessible_or_404(study_id, current_user, db)
    joined_changed = _mark_participant_room_joined(study.id, current_user, db)
    expired_changed = _expire_pending_turn_if_needed(study, db)
    if joined_changed or expired_changed:
        db.commit()
        db.refresh(study)
    return _detail_response(study, current_user, db)


@router.post(
    "/{study_id}/activate",
    response_model=GroupStudyDetailResponse,
    summary="Open a scheduled group study with Diya when the room is ready",
)
def activate_group_study(
    study_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    study = _study_accessible_or_404(study_id, current_user, db)
    joined_changed = _mark_participant_room_joined(study.id, current_user, db)
    expired_changed = _expire_pending_turn_if_needed(study, db)
    try:
        activated = _maybe_auto_start_study(study, db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Group study activation failed for study_id=%s", study.id)
        raise HTTPException(status_code=500, detail="Diya could not open this room yet. Please retry.") from exc
    if joined_changed or expired_changed or activated:
        db.commit()
        db.refresh(study)
    return _detail_response(study, current_user, db)


@router.post(
    "/{study_id}/submit-key",
    response_model=GroupStudyDetailResponse,
    summary="Submit Gemini API key for a group study",
)
def submit_group_study_key(
    study_id: UUID,
    payload: GroupStudySubmitKeyRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    study = _study_accessible_or_404(study_id, current_user, db)
    participant = _participant_row_for_user(study.id, current_user.id, db)
    if not participant:
        raise HTTPException(status_code=403, detail="Only invited student participants can submit a Gemini API key.")
    if participant.student_id is None:
        raise HTTPException(status_code=409, detail="Host-only users do not need to submit a Gemini API key.")
    try:
        normalized_key = normalize_gemini_key(payload.gemini_api_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    participant.gemini_api_key_encrypted = encrypt_gemini_key(normalized_key)
    participant.gemini_key_submitted_at = _now()
    participant.joined_at = participant.joined_at or _now()
    participant.status = "joined"
    try:
        _maybe_auto_start_study(study, db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Group study auto-start failed after key submission for study_id=%s", study.id)
        raise HTTPException(status_code=500, detail="Gemini key was saved, but Diya could not start the room yet.") from exc
    db.commit()
    db.refresh(study)
    return _detail_response(study, current_user, db)


@router.post(
    "/{study_id}/start",
    response_model=GroupStudyDetailResponse,
    summary="Start group study with Diya",
)
def start_group_study(
    study_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    study = _study_accessible_or_404(study_id, current_user, db)
    if study.creator_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the study creator can start this group study.")
    if study.status not in {"scheduled", "live"}:
        raise HTTPException(status_code=409, detail="This group study cannot be started.")
    try:
        _create_next_turn_or_finish(study, db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Manual group study start failed for study_id=%s", study.id)
        raise HTTPException(status_code=500, detail="Diya could not start this room.") from exc
    db.commit()
    db.refresh(study)
    return _detail_response(study, current_user, db)


@router.post(
    "/{study_id}/advance",
    response_model=GroupStudyDetailResponse,
    summary="Advance the group study to the next Diya step",
)
def advance_group_study(
    study_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    study = _study_accessible_or_404(study_id, current_user, db)
    if study.creator_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the study creator can advance the room.")
    if study.status not in {"scheduled", "live"}:
        raise HTTPException(status_code=409, detail="This group study is no longer active.")
    _create_next_turn_or_finish(study, db)
    db.commit()
    db.refresh(study)
    return _detail_response(study, current_user, db)


@router.post(
    "/{study_id}/turns/{turn_id}/answer",
    response_model=GroupStudyDetailResponse,
    summary="Submit an answer for the active group study turn",
)
def answer_group_study_turn(
    study_id: UUID,
    turn_id: UUID,
    payload: GroupStudyAnswerRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    study = _study_accessible_or_404(study_id, current_user, db)
    turn = db.query(GroupStudyTurn).filter(
        and_(
            GroupStudyTurn.id == turn_id,
            GroupStudyTurn.group_study_id == study.id,
        )
    ).first()
    if not turn:
        raise HTTPException(status_code=404, detail="Group study turn not found.")
    if _expire_pending_turn_if_needed(study, db, turn=turn):
        db.commit()
        db.refresh(study)
        raise HTTPException(status_code=409, detail="Time limit expired before the answer was submitted.")
    if turn.status != "pending":
        raise HTTPException(status_code=409, detail="This group study turn has already been answered.")
    if turn.target_user_id and current_user.id not in {turn.target_user_id, study.creator_user_id}:
        raise HTTPException(status_code=403, detail="Only the selected student or the creator can submit this answer.")

    target_participant = None
    if turn.target_user_id:
        target_participant = _participant_row_for_user(study.id, turn.target_user_id, db)
    elif current_user.role == "student":
        target_participant = _participant_row_for_user(study.id, current_user.id, db)
    if not target_participant:
        raise HTTPException(status_code=404, detail="Target participant not found for this group study.")

    evaluation = evaluate_group_study_answer(
        turn_type=turn.turn_type,
        answer_text=payload.answer_text,
        answer_choice=payload.answer_choice,
        correct_answer=turn.correct_answer,
        source_excerpt=turn.source_excerpt or "",
        question_text=turn.question_text or "",
    )
    turn.answer_text = payload.answer_text
    turn.answer_choice = payload.answer_choice
    turn.evaluation_data = {
        "feedback": evaluation.get("feedback"),
        "strengths": evaluation.get("strengths") or [],
        "improvement_areas": evaluation.get("improvement_areas") or [],
    }
    turn.score_awarded = float(evaluation.get("score") or 0.0)
    turn.is_correct = evaluation.get("is_correct")
    turn.status = "answered"
    turn.answered_at = _now()

    target_participant.total_score = float(target_participant.total_score or 0.0) + float(turn.score_awarded or 0.0)
    if turn.turn_type != "discussion_prompt":
        target_participant.total_questions = int(target_participant.total_questions or 0) + 1
    if turn.is_correct:
        target_participant.correct_answers = int(target_participant.correct_answers or 0) + 1
    target_participant.participation_count = int(target_participant.participation_count or 0) + 1
    target_participant.updated_at = _now()

    db.commit()
    db.refresh(study)
    return _detail_response(study, current_user, db)


@router.post(
    "/{study_id}/stop",
    response_model=GroupStudyDetailResponse,
    summary="Stop or complete the group study",
)
def stop_group_study(
    study_id: UUID,
    reason: Optional[str] = Query(None),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    study = _study_accessible_or_404(study_id, current_user, db)
    if study.status not in {"scheduled", "live"}:
        raise HTTPException(status_code=409, detail="This group study is already finished.")
    participant = _participant_row_for_user(study.id, current_user.id, db)
    if study.creator_user_id != current_user.id and participant is None:
        raise HTTPException(status_code=403, detail="Only room members can request or approve stopping this group study.")

    participant_rows = _participant_rows(study.id, db)
    required_approvers = _required_stop_approver_ids(study, participant_rows)
    approvals = _normalize_stop_approval_ids(study.stop_approvals_payload)
    active_request = bool(study.stop_requested_at and study.stop_requester_user_id)

    if active_request:
        if current_user.id in approvals:
            raise HTTPException(status_code=409, detail="You have already approved this stop request.")
        approvals.append(current_user.id)
        study.stop_approvals_payload = [str(user_id) for user_id in approvals]
        if all(user_id in approvals for user_id in required_approvers):
            _finalize_study(
                study,
                status="stopped",
                reason=study.stop_request_reason or "Group study stopped after room approval.",
                db=db,
            )
    elif study.creator_user_id == current_user.id and study.creator_role == "teacher":
        _finalize_study(
            study,
            status="stopped",
            reason=(reason or "").strip() or "Group study stopped by the teacher host.",
            db=db,
        )
    else:
        default_reason = (reason or "").strip() or f"{current_user.full_name} requested to stop the group study."
        study.stop_request_reason = default_reason[:255]
        study.stop_requester_user_id = current_user.id
        study.stop_requested_at = _now()
        study.stop_approvals_payload = [str(current_user.id)]

    db.commit()
    db.refresh(study)
    return _detail_response(study, current_user, db)
