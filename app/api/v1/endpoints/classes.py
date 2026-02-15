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
from app.models.student import Enrollment, StudentProfile
from app.models.subscription import Subscription
from app.models.teacher import TeacherProfile
from app.models.user import User
from app.schemas.class_ import (
    AttendanceListResponse,
    AttendanceResponse,
    ClassCreate,
    ClassListResponse,
    ClassResponse,
    ClassUpdate,
    MessageResponse,
)

router = APIRouter()


def _is_subscribed(user_id, db):
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _build_class_response(cls, teacher_profile, teacher_user, viewer, db):
    show_link = False
    link_gated = False
    if viewer:
        if viewer.role in ("teacher", "admin"):
            show_link = True
        elif viewer.role == "student":
            if _is_subscribed(viewer.id, db):
                show_link = True
            elif cls.meet_link:
                link_gated = True
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
        transcript_status=cls.transcript_status,
        notes_status=cls.notes_status,
        created_at=cls.created_at,
    )


@router.post("/", response_model=ClassResponse, status_code=201, summary="Create a class (teacher only)")
def create_class(payload: ClassCreate, current_user: User = Depends(require_teacher), db: Session = Depends(get_db)):
    teacher_profile = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    if not teacher_profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")
    cls = Class(
        teacher_id=teacher_profile.id,
        title=payload.title,
        subject=payload.subject,
        description=payload.description,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        batch_id=payload.batch_id,
        status="scheduled",
    )
    db.add(cls)
    teacher_profile.total_classes += 1
    db.commit()
    db.refresh(cls)
    return _build_class_response(cls, teacher_profile, current_user, current_user, db)


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
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(cls, field, value)
    cls.updated_at = datetime.now(timezone.utc)
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