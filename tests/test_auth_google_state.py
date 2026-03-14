from app.api.v1.endpoints.auth import (
    _build_google_state_payload,
    _parse_google_state_payload,
)


def test_google_state_round_trip_teacher_payload():
    state = _build_google_state_payload(
        mode="web",
        role="teacher",
        teacher_declaration_accepted=True,
        teacher_declaration_version="teacher-payout-v2026-03-07",
    )
    parsed = _parse_google_state_payload(state)
    assert parsed["mode"] == "web"
    assert parsed["role"] == "teacher"
    assert parsed["teacher_declaration_accepted"] is True
    assert parsed["teacher_declaration_version"] == "teacher-payout-v2026-03-07"


def test_google_state_parses_legacy_mode_only_values():
    assert _parse_google_state_payload("json")["mode"] == "json"
    assert _parse_google_state_payload("json")["role"] == "student"
    assert _parse_google_state_payload("web")["mode"] == "web"


def test_google_state_invalid_payload_falls_back_to_defaults():
    parsed = _parse_google_state_payload("tg.not-a-valid-state")
    assert parsed["mode"] == "web"
    assert parsed["role"] == "student"
    assert parsed["teacher_declaration_accepted"] is False
