from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import app.api.v1.endpoints.group_studies as group_studies_module
from app.api.v1.endpoints.group_studies import (
    activate_group_study,
    _normalized_study_sections,
    _participant_has_usable_api_key,
    _maybe_auto_start_study,
    _normalize_stop_approval_ids,
    _required_stop_approver_ids,
    _student_participants_ready,
    _stop_request_state,
    get_group_study,
)
from app.services.group_study_service import encrypt_gemini_key


def test_normalize_stop_approval_ids_skips_invalid_and_duplicates():
    first = uuid4()
    second = uuid4()

    result = _normalize_stop_approval_ids([str(first), "bad-value", str(first), second])

    assert result == [first, second]


def test_stop_request_state_reports_pending_members():
    creator_id = uuid4()
    other_id = uuid4()
    study = SimpleNamespace(
        creator_user_id=creator_id,
        stop_requested_at=datetime.now(timezone.utc),
        stop_requester_user_id=other_id,
        stop_request_reason="Need to stop now.",
        stop_approvals_payload=[str(other_id)],
    )
    participant_rows = [
        (SimpleNamespace(user_id=creator_id), SimpleNamespace(id=creator_id, full_name="Alice"), None),
        (SimpleNamespace(user_id=other_id), SimpleNamespace(id=other_id, full_name="Bob"), None),
    ]
    current_user = SimpleNamespace(id=creator_id, full_name="Alice")

    state = _stop_request_state(study, current_user, participant_rows, db=None)

    assert _required_stop_approver_ids(study, participant_rows) == [creator_id, other_id]
    assert state["stop_request_active"] is True
    assert state["stop_request_approvals"] == 1
    assert state["stop_request_required"] == 2
    assert state["current_user_has_approved_stop"] is False
    assert state["stop_request_pending_names"] == ["Alice"]


def test_student_participants_ready_requires_all_students_to_join_and_submit_key():
    ready_student = SimpleNamespace(
        student_id=uuid4(),
        joined_at=datetime.now(timezone.utc),
        gemini_api_key_encrypted=encrypt_gemini_key("a" * 32),
    )
    waiting_student = SimpleNamespace(
        student_id=uuid4(),
        joined_at=None,
        gemini_api_key_encrypted=None,
    )

    assert _student_participants_ready([
        (ready_student, SimpleNamespace(full_name="Ready"), None),
        (waiting_student, SimpleNamespace(full_name="Waiting"), None),
    ]) is False


def test_participant_has_usable_api_key_rejects_invalid_ciphertext():
    participant = SimpleNamespace(gemini_api_key_encrypted="not-a-valid-token")

    assert _participant_has_usable_api_key(participant) is False


def test_normalized_study_sections_accepts_legacy_shapes():
    study = SimpleNamespace(
        title="Iran vs Israel",
        description="Topic outline",
        document_text="",
        sections_payload={"sections": ["First point", {"title": "Second", "text": "Second point"}]},
    )

    sections = _normalized_study_sections(study)

    assert len(sections) == 2
    assert sections[0]["text"] == "First point"
    assert sections[1]["title"] == "Second"


def test_maybe_auto_start_study_starts_once_room_is_ready(monkeypatch):
    study = SimpleNamespace(id=uuid4(), status="scheduled")
    participant_rows = [
        (
            SimpleNamespace(
                student_id=uuid4(),
                joined_at=datetime.now(timezone.utc),
                gemini_api_key_encrypted=encrypt_gemini_key("b" * 32),
            ),
            SimpleNamespace(full_name="Pro Student"),
            None,
        )
    ]
    started = {"count": 0}

    monkeypatch.setattr(group_studies_module, "_participant_rows", lambda study_id, db: participant_rows)
    monkeypatch.setattr(group_studies_module, "_pending_group_study_turn", lambda study_id, db: None)
    monkeypatch.setattr(
        group_studies_module,
        "_create_next_turn_or_finish",
        lambda room, db: started.__setitem__("count", started["count"] + 1),
    )

    assert _maybe_auto_start_study(study, db=object()) is True
    assert started["count"] == 1


def test_maybe_auto_start_study_skips_until_room_is_ready(monkeypatch):
    study = SimpleNamespace(id=uuid4(), status="scheduled")
    participant_rows = [
        (
            SimpleNamespace(
                student_id=uuid4(),
                joined_at=datetime.now(timezone.utc),
                gemini_api_key_encrypted=None,
            ),
            SimpleNamespace(full_name="Pro Student"),
            None,
        )
    ]

    monkeypatch.setattr(group_studies_module, "_participant_rows", lambda study_id, db: participant_rows)
    monkeypatch.setattr(group_studies_module, "_create_next_turn_or_finish", lambda room, db: (_ for _ in ()).throw(AssertionError("should not auto-start")))

    assert _maybe_auto_start_study(study, db=object()) is False


def test_get_group_study_does_not_auto_start(monkeypatch):
    study_id = uuid4()
    current_user = SimpleNamespace(id=uuid4(), full_name="Student")
    study = SimpleNamespace(id=study_id, status="scheduled")
    db = SimpleNamespace(commit=lambda: None, refresh=lambda room: None)

    monkeypatch.setattr(group_studies_module, "_study_accessible_or_404", lambda sid, user, session: study)
    monkeypatch.setattr(group_studies_module, "_mark_participant_room_joined", lambda sid, user, session: False)
    monkeypatch.setattr(group_studies_module, "_expire_pending_turn_if_needed", lambda room, session: False)
    monkeypatch.setattr(group_studies_module, "_detail_response", lambda room, user, session: {"id": str(room.id)})
    monkeypatch.setattr(
        group_studies_module,
        "_maybe_auto_start_study",
        lambda room, session: (_ for _ in ()).throw(AssertionError("GET should not auto-start the room")),
    )

    result = get_group_study(study_id, current_user=current_user, db=db)

    assert result == {"id": str(study_id)}


def test_activate_group_study_attempts_room_activation(monkeypatch):
    study_id = uuid4()
    current_user = SimpleNamespace(id=uuid4(), full_name="Student")
    study = SimpleNamespace(id=study_id, status="scheduled")
    commits = {"count": 0}
    refreshes = {"count": 0}
    db = SimpleNamespace(
        commit=lambda: commits.__setitem__("count", commits["count"] + 1),
        refresh=lambda room: refreshes.__setitem__("count", refreshes["count"] + 1),
    )

    monkeypatch.setattr(group_studies_module, "_study_accessible_or_404", lambda sid, user, session: study)
    monkeypatch.setattr(group_studies_module, "_mark_participant_room_joined", lambda sid, user, session: False)
    monkeypatch.setattr(group_studies_module, "_expire_pending_turn_if_needed", lambda room, session: False)
    monkeypatch.setattr(group_studies_module, "_maybe_auto_start_study", lambda room, session: True)
    monkeypatch.setattr(group_studies_module, "_detail_response", lambda room, user, session: {"status": room.status})

    result = activate_group_study(study_id, current_user=current_user, db=db)

    assert result == {"status": "scheduled"}
    assert commits["count"] == 1
    assert refreshes["count"] == 1
