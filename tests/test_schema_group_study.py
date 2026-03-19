import pytest
from pydantic import ValidationError

from app.schemas.group_study import GroupStudyAnswerRequest, GroupStudyCreatePayload


def test_group_study_create_payload_requires_topic_or_document():
    with pytest.raises(ValidationError, match="Provide either a topic outline or an uploaded discussion document."):
        GroupStudyCreatePayload(
            title="Algebra room",
            subject="mathematics",
            scheduled_at="2026-03-16T18:00:00Z",
        )


def test_group_study_create_payload_accepts_topic_outline():
    payload = GroupStudyCreatePayload(
        title="Algebra room",
        subject="mathematics",
        scheduled_at="2026-03-16T18:00:00Z",
        topic_outline="Linear equations and word problems.",
    )

    assert payload.title == "Algebra room"
    assert payload.topic_outline == "Linear equations and word problems."


def test_group_study_answer_request_requires_answer():
    with pytest.raises(ValidationError, match="Provide answer_text or answer_choice."):
        GroupStudyAnswerRequest()
