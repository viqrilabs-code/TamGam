# app/api/v1/endpoints/classes.py
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_teacher
from app.db.session import get_db
from app.models.class_ import Attendance, Class
from app.models.notification import Notification
from app.models.student import Batch, BatchMember, Enrollment, StudentProfile
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.schemas.class_ import (
    AttendanceListResponse,
    AttendanceResponse,
    BatchAddStudentsRequest,
    BatchCancelDayRequest,
    BatchCreateRequest,
    BatchEnrolledStudentListResponse,
    BatchListResponse,
    BatchStudentResponse,
    BatchSummaryResponse,
    BatchUpdateRequest,
    ClassCreate,
    ClassListResponse,
    ClassResponse,
    ClassUpdate,
    MessageResponse,
)

router = APIRouter()

def _build_class_response(cls, teacher_profile, teacher_user, viewer, db):
    show_link = False
    link_gated = False
    if viewer:
        if viewer.role in ("teacher", "admin"):
            show_link = True
        elif viewer.role == "student":
            # Students receive classes only for their enrolled teachers.
            # If a live link exists, expose it so enrolled students can join.
            show_link = bool(cls.meet_link)
    return ClassResponse(
        id=cls.id,
        title=cls.title,
        subject=cls.subject,
        description=cls.description,
        teacher_id=teacher_profile.id,
        teacher_name=teacher_user.full_name,
        teacher_avatar_url=teacher_user.avatar_url,
        teacher_is_verified=teacher_profile.is_verified,
        batch_id=cls.batch_id,
        scheduled_at=cls.scheduled_at,
        duration_minutes=cls.duration_minutes,
        status=cls.status,
        meet_link=cls.meet_link if show_link else None,
        meet_link_gated=link_gated,
        transcript_status="ready" if cls.transcript_processed else "pending",
        notes_status="ready" if cls.notes_generated else "pending",
        created_at=cls.created_at,
    )


def _get_teacher_profile_or_404(user_id: UUID, db: Session) -> TeacherProfile:
    teacher_profile = db.query(TeacherProfile).filter(TeacherProfile.user_id == user_id).first()
    if not teacher_profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    return teacher_profile


def _get_enrolled_students_for_teacher(
    teacher_id: UUID,
    db: Session,
    grade_level: Optional[int] = None,
):
    query = db.query(Enrollment, StudentProfile, User).join(
        StudentProfile, StudentProfile.id == Enrollment.student_id
    ).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        and_(
            Enrollment.teacher_id == teacher_id,
            Enrollment.is_active == True,
        )
    )
    if grade_level is not None:
        query = query.filter(StudentProfile.grade == grade_level)
    return query.all()


def _build_enrolled_student_items(enrollment_rows) -> list[BatchStudentResponse]:
    grouped = {}
    for enrollment, student_profile, student_user in enrollment_rows:
        key = str(student_profile.id)
        if key not in grouped:
            grouped[key] = BatchStudentResponse(
                student_id=student_profile.id,
                user_id=student_user.id,
                full_name=student_user.full_name,
                avatar_url=student_user.avatar_url,
                grade=student_profile.grade,
                enrolled_subjects=[],
            )
        if enrollment.subject and enrollment.subject not in grouped[key].enrolled_subjects:
            grouped[key].enrolled_subjects.append(enrollment.subject)
    return list(grouped.values())


def _build_batch_summary(batch: Batch, teacher_id: UUID, db: Session) -> BatchSummaryResponse:
    member_rows = db.query(BatchMember, StudentProfile, User).join(
        StudentProfile, StudentProfile.id == BatchMember.student_id
    ).join(
        User, User.id == StudentProfile.user_id
    ).filter(
        BatchMember.batch_id == batch.id
    ).all()

    enrolled_rows = _get_enrolled_students_for_teacher(teacher_id, db, grade_level=None)
    enrolled_items = _build_enrolled_student_items(enrolled_rows)
    enrolled_map = {str(item.student_id): item for item in enrolled_items}

    members: list[BatchStudentResponse] = []
    for _, student_profile, student_user in member_rows:
        cached = enrolled_map.get(str(student_profile.id))
        members.append(
            BatchStudentResponse(
                student_id=student_profile.id,
                user_id=student_user.id,
                full_name=student_user.full_name,
                avatar_url=student_user.avatar_url,
                grade=student_profile.grade,
                enrolled_subjects=cached.enrolled_subjects if cached else [],
            )
        )

    return BatchSummaryResponse(
        id=batch.id,
        name=batch.name,
        subject=batch.subject,
        class_timing=batch.default_timing,
        description=batch.description,
        grade_level=batch.grade_level,
        student_selection_enabled=batch.student_selection_enabled,
        max_students=batch.max_students,
        class_days=batch.class_days or [],
        cancelled_days=batch.cancelled_days or [],
        is_active=batch.is_active,
        member_count=len(members),
        created_at=batch.created_at,
        members=members,
    )


@router.post("/", response_model=ClassResponse, status_code=201, summary="Create a class (teacher only)")
def create_class(payload: ClassCreate, current_user: User = Depends(require_teacher), db: Session = Depends(get_db)):
    teacher_profile = _get_teacher_profile_or_404(current_user.id, db)
    selected_batch = None
    if payload.batch_id:
        selected_batch = db.query(Batch).filter(
            and_(
                Batch.id == payload.batch_id,
                Batch.teacher_id == teacher_profile.id,
            )
        ).first()
        if not selected_batch:
            raise HTTPException(status_code=404, detail="Batch not found.")
        if not selected_batch.is_active:
            raise HTTPException(status_code=409, detail="Batch is inactive.")

    cls = Class(
        teacher_id=teacher_profile.id,
        title=payload.title,
        subject=payload.subject,
        description=payload.description,
        meet_link=payload.meet_link,
        grade_level=selected_batch.grade_level if selected_batch else None,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        batch_id=payload.batch_id,
        status="scheduled",
    )
    db.add(cls)
    db.flush()

    recipient_user_ids: set[UUID] = set()
    if selected_batch:
        batch_user_rows = db.query(User.id).join(
            StudentProfile, StudentProfile.user_id == User.id
        ).join(
            BatchMember, BatchMember.student_id == StudentProfile.id
        ).filter(
            BatchMember.batch_id == selected_batch.id
        ).all()
        recipient_user_ids = {row[0] for row in batch_user_rows}
    else:
        enrolled_rows = _get_enrolled_students_for_teacher(teacher_profile.id, db, grade_level=None)
        recipient_user_ids = {item.user_id for item in _build_enrolled_student_items(enrolled_rows)}

    schedule_label = cls.scheduled_at.strftime("%d %b %Y %I:%M %p UTC")
    for user_id in recipient_user_ids:
        db.add(
            Notification(
                user_id=user_id,
                notification_type="announcement",
                title=f"New class scheduled: {cls.title}",
                body=(
                    f"{current_user.full_name} scheduled {cls.subject} on {schedule_label}."
                    + (f" Batch: {selected_batch.name}." if selected_batch else "")
                ),
                action_url="/dashboard.html",
                extra_data={
                    "kind": "class_scheduled",
                    "class_id": str(cls.id),
                    "batch_id": str(selected_batch.id) if selected_batch else None,
                },
            )
        )

    teacher_profile.total_classes += 1
    db.commit()
    db.refresh(cls)
    return _build_class_response(cls, teacher_profile, current_user, current_user, db)


@router.get("/batches/enrolled-students", response_model=BatchEnrolledStudentListResponse, summary="List enrolled students for batches (teacher only)")
def list_batch_enrolled_students(
    grade_level: Optional[int] = Query(None),
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    if grade_level is not None and grade_level not in (8, 9, 10):
        raise HTTPException(status_code=422, detail="grade_level must be one of 8, 9, 10")

    teacher_profile = _get_teacher_profile_or_404(current_user.id, db)
    rows = _get_enrolled_students_for_teacher(teacher_profile.id, db, grade_level=grade_level)
    students = _build_enrolled_student_items(rows)
    return BatchEnrolledStudentListResponse(students=students, total=len(students))


@router.get("/batches", response_model=BatchListResponse, summary="List teacher batches")
def list_batches(
    grade_level: Optional[int] = Query(None),
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    if grade_level is not None and grade_level not in (8, 9, 10):
        raise HTTPException(status_code=422, detail="grade_level must be one of 8, 9, 10")

    teacher_profile = _get_teacher_profile_or_404(current_user.id, db)
    query = db.query(Batch).filter(Batch.teacher_id == teacher_profile.id)
    if grade_level is not None:
        query = query.filter(Batch.grade_level == grade_level)
    batches = query.order_by(Batch.created_at.desc()).all()
    items = [_build_batch_summary(batch, teacher_profile.id, db) for batch in batches]
    return BatchListResponse(batches=items, total=len(items))


@router.post("/batches", response_model=BatchSummaryResponse, status_code=201, summary="Create batch")
def create_batch(
    payload: BatchCreateRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher_profile = _get_teacher_profile_or_404(current_user.id, db)
    existing = db.query(Batch).filter(
        and_(
            Batch.teacher_id == teacher_profile.id,
            Batch.name == payload.name.strip(),
            Batch.grade_level == payload.grade_level,
            Batch.is_active == True,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="A batch with this name already exists for this class.")

    batch = Batch(
        teacher_id=teacher_profile.id,
        name=payload.name.strip(),
        subject=payload.subject.strip() if payload.subject else None,
        default_timing=payload.class_timing.strip() if payload.class_timing else None,
        description=payload.description.strip() if payload.description else None,
        grade_level=payload.grade_level,
        max_students=payload.max_students,
        class_days=[d.strip().lower() for d in (payload.class_days or []) if d and d.strip()],
        cancelled_days=[],
        is_active=True,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return _build_batch_summary(batch, teacher_profile.id, db)


@router.post("/batches/{batch_id}/students", response_model=BatchSummaryResponse, summary="Add students to batch")
def add_students_to_batch(
    batch_id: UUID,
    payload: BatchAddStudentsRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher_profile = _get_teacher_profile_or_404(current_user.id, db)
    batch = db.query(Batch).filter(
        and_(
            Batch.id == batch_id,
            Batch.teacher_id == teacher_profile.id,
        )
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
    if not batch.is_active:
        raise HTTPException(status_code=409, detail="Batch is inactive.")
    if batch.max_students is not None:
        current_count = db.query(BatchMember).filter(BatchMember.batch_id == batch.id).count()
        available_slots = batch.max_students - current_count
        if available_slots <= 0:
            raise HTTPException(status_code=409, detail="Batch capacity reached.")

    target_ids = list({sid for sid in payload.student_ids})
    if batch.max_students is not None:
        current_count = db.query(BatchMember).filter(BatchMember.batch_id == batch.id).count()
        available_slots = batch.max_students - current_count
        target_ids = target_ids[: max(available_slots, 0)]
        if not target_ids:
            raise HTTPException(status_code=409, detail="Batch capacity reached.")
    rows = db.query(Enrollment, StudentProfile).join(
        StudentProfile, StudentProfile.id == Enrollment.student_id
    ).filter(
        and_(
            Enrollment.teacher_id == teacher_profile.id,
            Enrollment.student_id.in_(target_ids),
            Enrollment.is_active == True,
        )
    ).all()

    enrolled_student_ids = {row[1].id for row in rows}
    missing = [sid for sid in target_ids if sid not in enrolled_student_ids]
    if missing:
        raise HTTPException(status_code=404, detail="One or more students are not actively enrolled with you.")

    if batch.grade_level is not None:
        invalid_grade = [sp.id for _, sp in rows if sp.grade != batch.grade_level]
        if invalid_grade:
            raise HTTPException(
                status_code=409,
                detail=f"All students must belong to class {batch.grade_level} for this batch.",
            )

    existing_members = db.query(BatchMember.student_id).filter(
        BatchMember.batch_id == batch.id
    ).all()
    existing_member_ids = {row[0] for row in existing_members}

    for student_id in target_ids:
        if student_id in existing_member_ids:
            continue
        db.add(BatchMember(batch_id=batch.id, student_id=student_id))

    db.commit()
    db.refresh(batch)
    return _build_batch_summary(batch, teacher_profile.id, db)


@router.patch("/batches/{batch_id}", response_model=BatchSummaryResponse, summary="Update batch settings")
def update_batch(
    batch_id: UUID,
    payload: BatchUpdateRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher_profile = _get_teacher_profile_or_404(current_user.id, db)
    batch = db.query(Batch).filter(
        and_(
            Batch.id == batch_id,
            Batch.teacher_id == teacher_profile.id,
        )
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")

    data = payload.model_dump(exclude_none=True)
    if "subject" in data:
        batch.subject = data["subject"].strip() if data["subject"] else None
    if "class_timing" in data:
        batch.default_timing = data["class_timing"].strip() if data["class_timing"] else None
    if "description" in data:
        batch.description = data["description"].strip() if data["description"] else None
    if "student_selection_enabled" in data:
        batch.student_selection_enabled = bool(data["student_selection_enabled"])
    if "max_students" in data:
        new_limit = data["max_students"]
        if new_limit is not None:
            current_members = db.query(BatchMember).filter(BatchMember.batch_id == batch.id).count()
            if new_limit < current_members:
                raise HTTPException(status_code=409, detail="max_students cannot be lower than current member count.")
        batch.max_students = new_limit
    if "class_days" in data:
        normalized = [d.strip().lower() for d in (data["class_days"] or []) if d and d.strip()]
        batch.class_days = normalized
        existing_cancelled = set(batch.cancelled_days or [])
        batch.cancelled_days = [d for d in normalized if d in existing_cancelled]

    db.commit()
    db.refresh(batch)
    return _build_batch_summary(batch, teacher_profile.id, db)


@router.post("/batches/{batch_id}/cancel-day", response_model=MessageResponse, summary="Cancel a class day for a batch")
def cancel_batch_day(
    batch_id: UUID,
    payload: BatchCancelDayRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher_profile = _get_teacher_profile_or_404(current_user.id, db)
    batch = db.query(Batch).filter(
        and_(
            Batch.id == batch_id,
            Batch.teacher_id == teacher_profile.id,
        )
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")

    class_days = set((batch.class_days or []))
    if payload.day not in class_days:
        raise HTTPException(status_code=409, detail="Day is not configured for this batch.")

    cancelled = set((batch.cancelled_days or []))
    if payload.day in cancelled:
        raise HTTPException(status_code=409, detail="This day is already cancelled.")
    cancelled.add(payload.day)
    batch.cancelled_days = sorted(cancelled)

    batch_students = db.query(User.id).join(
        StudentProfile, StudentProfile.user_id == User.id
    ).join(
        BatchMember, BatchMember.student_id == StudentProfile.id
    ).filter(
        BatchMember.batch_id == batch.id
    ).all()

    readable_day = payload.day.capitalize()
    for row in batch_students:
        db.add(
            Notification(
                user_id=row[0],
                notification_type="announcement",
                title=f"Batch class cancelled on {readable_day}",
                body=(
                    f"{current_user.full_name} cancelled {batch.name} class on {readable_day}."
                    + (f" Note: {payload.note}" if payload.note else "")
                ),
                action_url="/dashboard.html",
                extra_data={
                    "kind": "batch_day_cancelled",
                    "batch_id": str(batch.id),
                    "day": payload.day,
                },
            )
        )

    db.commit()
    return MessageResponse(message=f"{readable_day} cancelled for {batch.name}.")


@router.delete("/batches/{batch_id}", response_model=MessageResponse, summary="Delete batch (teacher only)")
def delete_batch(
    batch_id: UUID,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    teacher_profile = _get_teacher_profile_or_404(current_user.id, db)
    batch = db.query(Batch).filter(
        and_(
            Batch.id == batch_id,
            Batch.teacher_id == teacher_profile.id,
        )
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")

    # Soft delete so historical classes remain linked.
    batch.is_active = False
    db.commit()
    return MessageResponse(message="Batch deleted.")


@router.get("/", response_model=ClassListResponse, summary="List classes")
def list_classes(
    subject: Optional[str] = Query(None),
    class_status: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    query = db.query(Class, TeacherProfile, User).join(
        TeacherProfile, TeacherProfile.id == Class.teacher_id
    ).join(User, User.id == TeacherProfile.user_id)

    if current_user.role == "teacher":
        tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
        if not tp:
            return ClassListResponse(classes=[], total=0)
        query = query.filter(Class.teacher_id == tp.id)
    elif current_user.role == "student":
        sp = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
        if not sp:
            return ClassListResponse(classes=[], total=0)
        teacher_ids = [t[0] for t in db.query(Enrollment.teacher_id).filter(
            and_(Enrollment.student_id == sp.id, Enrollment.is_active == True)
        ).all()]
        if not teacher_ids:
            return ClassListResponse(classes=[], total=0)
        query = query.filter(Class.teacher_id.in_(teacher_ids))

    if subject:
        query = query.filter(Class.subject == subject)
    if class_status:
        query = query.filter(Class.status == class_status)

    total = query.count()
    results = query.order_by(Class.scheduled_at.desc()).offset(skip).limit(limit).all()

    return ClassListResponse(
        classes=[_build_class_response(c, tp, u, current_user, db) for c, tp, u in results],
        total=total,
    )


@router.get("/mine", response_model=ClassListResponse, summary="List my classes")
def list_my_classes(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Convenience endpoint for dashboards; role-aware like /classes but without extra filters."""
    return list_classes(
        subject=None,
        class_status=None,
        skip=skip,
        limit=limit,
        current_user=current_user,
        db=db,
    )


@router.get("/{class_id}", response_model=ClassResponse, summary="Get class detail")
def get_class(class_id: UUID, current_user: User = Depends(require_login), db: Session = Depends(get_db)):
    cls = db.query(Class).filter(Class.id == class_id).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")
    tp = db.query(TeacherProfile).filter(TeacherProfile.id == cls.teacher_id).first()
    tu = db.query(User).filter(User.id == tp.user_id).first()
    return _build_class_response(cls, tp, tu, current_user, db)


@router.patch("/{class_id}", response_model=ClassResponse, summary="Update class (teacher only)")
def update_class(
    class_id: UUID,
    payload: ClassUpdate,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(and_(Class.id == class_id, Class.teacher_id == tp.id)).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")
    previous_scheduled_at = cls.scheduled_at
    previous_batch_id = cls.batch_id
    previous_meet_link = cls.meet_link
    updates = payload.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cls, field, value)
    cls.updated_at = datetime.now(timezone.utc)

    scheduled_changed = "scheduled_at" in updates and updates["scheduled_at"] != previous_scheduled_at
    meet_link_changed = (
        "meet_link" in updates
        and bool(str(updates["meet_link"]).strip() if updates["meet_link"] is not None else "")
        and updates["meet_link"] != previous_meet_link
    )
    recipient_user_ids: set[UUID] = set()
    effective_batch_id = cls.batch_id or previous_batch_id
    if effective_batch_id:
        batch_rows = db.query(User.id).join(
            StudentProfile, StudentProfile.user_id == User.id
        ).join(
            BatchMember, BatchMember.student_id == StudentProfile.id
        ).filter(
            BatchMember.batch_id == effective_batch_id
        ).all()
        recipient_user_ids = {row[0] for row in batch_rows}
    else:
        enrolled_rows = _get_enrolled_students_for_teacher(tp.id, db, grade_level=None)
        recipient_user_ids = {item.user_id for item in _build_enrolled_student_items(enrolled_rows)}

    if scheduled_changed:
        schedule_label = cls.scheduled_at.strftime("%d %b %Y %I:%M %p UTC")
        for user_id in recipient_user_ids:
            db.add(
                Notification(
                    user_id=user_id,
                    notification_type="announcement",
                    title=f"Class rescheduled: {cls.title}",
                    body=f"{current_user.full_name} rescheduled this class to {schedule_label}.",
                    action_url="/dashboard.html",
                    extra_data={
                        "kind": "class_rescheduled",
                        "class_id": str(cls.id),
                        "batch_id": str(effective_batch_id) if effective_batch_id else None,
                    },
                )
            )

    if meet_link_changed:
        for user_id in recipient_user_ids:
            db.add(
                Notification(
                    user_id=user_id,
                    notification_type="announcement",
                    title=f"Live class link updated: {cls.title}",
                    body=f"{current_user.full_name} updated the live class link. Open the class card to join.",
                    action_url="/dashboard.html",
                    extra_data={
                        "kind": "class_link_updated",
                        "class_id": str(cls.id),
                        "batch_id": str(effective_batch_id) if effective_batch_id else None,
                    },
                )
            )

    db.commit()
    db.refresh(cls)
    return _build_class_response(cls, tp, current_user, current_user, db)


@router.delete("/{class_id}", response_model=MessageResponse, summary="Cancel class (teacher only)")
def cancel_class(
    class_id: UUID,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(and_(Class.id == class_id, Class.teacher_id == tp.id)).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")
    if cls.status == "completed":
        raise HTTPException(status_code=409, detail="Cannot cancel a completed class.")
    cls.status = "cancelled"

    recipient_user_ids: set[UUID] = set()
    if cls.batch_id:
        batch_rows = db.query(User.id).join(
            StudentProfile, StudentProfile.user_id == User.id
        ).join(
            BatchMember, BatchMember.student_id == StudentProfile.id
        ).filter(
            BatchMember.batch_id == cls.batch_id
        ).all()
        recipient_user_ids = {row[0] for row in batch_rows}
    else:
        enrolled_rows = _get_enrolled_students_for_teacher(tp.id, db, grade_level=None)
        recipient_user_ids = {item.user_id for item in _build_enrolled_student_items(enrolled_rows)}

    for user_id in recipient_user_ids:
        db.add(
            Notification(
                user_id=user_id,
                notification_type="announcement",
                title=f"Class cancelled: {cls.title}",
                body=f"{current_user.full_name} cancelled this class.",
                action_url="/dashboard.html",
                extra_data={
                    "kind": "class_cancelled",
                    "class_id": str(cls.id),
                    "batch_id": str(cls.batch_id) if cls.batch_id else None,
                },
            )
        )
    db.commit()
    return MessageResponse(message="Class cancelled.")


@router.post("/{class_id}/attendance", response_model=AttendanceResponse, status_code=201, summary="Mark own attendance")
def mark_attendance(
    class_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    sp = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Student profile not found.")
    cls = db.query(Class).filter(Class.id == class_id).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")
    if cls.status not in ("scheduled", "live"):
        raise HTTPException(status_code=409, detail="Attendance can only be marked for scheduled or live classes.")
    existing = db.query(Attendance).filter(
        and_(Attendance.class_id == class_id, Attendance.student_id == sp.id)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Attendance already marked.")
    attendance = Attendance(
        class_id=class_id,
        student_id=sp.id,
        joined_at=datetime.now(timezone.utc),
        marked_by="student",
    )
    db.add(attendance)
    db.commit()
    db.refresh(attendance)
    return AttendanceResponse(
        id=attendance.id,
        class_id=attendance.class_id,
        student_id=attendance.student_id,
        student_name=current_user.full_name,
        student_avatar_url=current_user.avatar_url,
        joined_at=attendance.joined_at,
        duration_minutes=attendance.duration_minutes,
        marked_by=attendance.marked_by,
    )


@router.get("/{class_id}/attendance", response_model=AttendanceListResponse, summary="List attendance (teacher only)")
def list_attendance(
    class_id: UUID,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(and_(Class.id == class_id, Class.teacher_id == tp.id)).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    records = db.query(Attendance, StudentProfile, User).join(
        StudentProfile, StudentProfile.id == Attendance.student_id
    ).join(User, User.id == StudentProfile.user_id).filter(
        Attendance.class_id == class_id
    ).all()

    total_enrolled = db.query(Enrollment).filter(
        and_(Enrollment.teacher_id == tp.id, Enrollment.is_active == True)
    ).count()

    return AttendanceListResponse(
        class_id=class_id,
        total_enrolled=total_enrolled,
        total_present=len(records),
        attendance=[
            AttendanceResponse(
                id=att.id,
                class_id=att.class_id,
                student_id=att.student_id,
                student_name=user.full_name,
                student_avatar_url=user.avatar_url,
                joined_at=att.joined_at,
                duration_minutes=att.duration_minutes,
                marked_by=att.marked_by,
            )
            for att, sp, user in records
        ],
    )
