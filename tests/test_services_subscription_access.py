from uuid import uuid4

from app.services.subscription_access import can_allocate_teacher_seat


def test_can_allocate_teacher_seat_allows_existing_teacher_without_extra_seat():
    teacher_id = uuid4()
    assert can_allocate_teacher_seat(
        seat_count=1,
        active_teacher_ids={teacher_id},
        target_teacher_id=teacher_id,
    ) is True


def test_can_allocate_teacher_seat_enforces_capacity_for_new_teacher():
    t1 = uuid4()
    t2 = uuid4()
    assert can_allocate_teacher_seat(
        seat_count=1,
        active_teacher_ids={t1},
        target_teacher_id=t2,
    ) is False
    assert can_allocate_teacher_seat(
        seat_count=2,
        active_teacher_ids={t1},
        target_teacher_id=t2,
    ) is True
