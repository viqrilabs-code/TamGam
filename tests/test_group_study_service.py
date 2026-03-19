from uuid import uuid4

from app.services.gemini_key_manager import GeminiQuotaExhausted
from app.services.group_study_service import (
    build_group_study_report,
    decrypt_gemini_key,
    encrypt_gemini_key,
    evaluate_group_study_answer,
    execute_with_group_study_keys,
    infer_section_difficulty,
    resolve_turn_time_limit_seconds,
    sectionize_content,
)


def test_encrypt_and_decrypt_gemini_key_roundtrip():
    raw = "a" * 32
    token = encrypt_gemini_key(raw)

    assert token != raw
    assert decrypt_gemini_key(token) == raw


def test_execute_with_group_study_keys_rotates_on_quota():
    calls = []

    def operation(key):
      calls.append(key)
      if key == "first-key":
        raise GeminiQuotaExhausted("quota")
      return "ok"

    result = execute_with_group_study_keys(["first-key", "second-key"], operation)

    assert result == "ok"
    assert calls == ["first-key", "second-key"]


def test_sectionize_content_splits_large_text():
    text = "\n\n".join(
        f"Heading {idx}\nThis section explains concept {idx}. It includes details and worked examples."
        for idx in range(1, 8)
    )

    sections = sectionize_content(title="Science room", source_text=text, max_sections=4, target_chars=120)

    assert len(sections) <= 4
    assert all(section["text"] for section in sections)


def test_evaluate_group_study_answer_marks_mcq_correct():
    result = evaluate_group_study_answer(
        turn_type="mcq_question",
        answer_text=None,
        answer_choice="B",
        correct_answer="B",
        source_excerpt="An algebraic expression uses variables and constants.",
        question_text="Which option is correct?",
    )

    assert result["score"] == 1.0
    assert result["is_correct"] is True


def test_build_group_study_report_selects_winner_and_feedback():
    alice_id = uuid4()
    bob_id = uuid4()
    report = build_group_study_report(
        title="English room",
        participants=[
            {
                "user_id": alice_id,
                "full_name": "Alice",
                "total_score": 4.0,
                "total_questions": 2,
                "correct_answers": 1,
                "participation_count": 2,
            },
            {
                "user_id": bob_id,
                "full_name": "Bob",
                "total_score": 2.0,
                "total_questions": 2,
                "correct_answers": 0,
                "participation_count": 1,
            },
        ],
        turns=[
            {
                "target_user_id": alice_id,
                "evaluation_data": {
                    "strengths": ["Used clear evidence."],
                    "improvement_areas": ["Add one more example."],
                },
            },
            {
                "target_user_id": bob_id,
                "evaluation_data": {
                    "strengths": [],
                    "improvement_areas": ["Explain the main idea more clearly."],
                },
            },
        ],
    )

    assert report["winner_name"] == "Alice"
    assert report["winner_user_id"] == str(alice_id)
    assert report["participants"][0]["user_id"] == str(alice_id)
    assert report["participants"][0]["strengths"] == ["Used clear evidence."]
    assert "tamgam.in" in report["share_caption"]


def test_build_group_study_report_skips_winner_when_no_activity():
    alice_id = uuid4()
    bob_id = uuid4()
    report = build_group_study_report(
        title="Math room",
        participants=[
            {
                "user_id": alice_id,
                "full_name": "Alice",
                "total_score": 0.0,
                "total_questions": 0,
                "correct_answers": 0,
                "participation_count": 0,
            },
            {
                "user_id": bob_id,
                "full_name": "Bob",
                "total_score": 0.0,
                "total_questions": 0,
                "correct_answers": 0,
                "participation_count": 0,
            },
        ],
        turns=[],
    )

    assert report["winner_name"] is None
    assert report["winner_user_id"] is None
    assert "without enough responses" in report["summary"]


def test_infer_section_difficulty_and_time_limits_for_stem():
    section = {
        "title": "Kinematics challenge",
        "text": "Analyse the experiment, compare the two motions, and justify the final velocity using evidence from the table.",
        "word_count": 18,
    }

    difficulty = infer_section_difficulty(section, "stem")

    assert difficulty == "hard"
    assert resolve_turn_time_limit_seconds("stem", difficulty) == 30


def test_resolve_turn_time_limit_seconds_for_english():
    assert resolve_turn_time_limit_seconds("english", "easy") == 20
    assert resolve_turn_time_limit_seconds("english", "medium") == 25
    assert resolve_turn_time_limit_seconds("english", "hard") == 30
