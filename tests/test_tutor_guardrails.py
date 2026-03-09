from app.api.v1.endpoints import tutor


def test_guardrail_allows_greetings():
    assert tutor._guardrail_check("Hello") is None
    assert tutor._guardrail_check("thanks!") is None
    assert tutor._guardrail_check("good evening") is None


def test_guardrail_allows_class_5_to_10_study_query():
    assert tutor._guardrail_check("Can you explain fractions for class 5?") is None
    assert tutor._guardrail_check("I need help with grade 10 science.") is None


def test_guardrail_allows_academic_weakness_query():
    assert tutor._guardrail_check("Hi Diya! Tell me about my weaknesses.") is None
    assert tutor._guardrail_check("Can you make a study plan for my weak subjects?") is None


def test_guardrail_blocks_explicit_content():
    reason, message = tutor._guardrail_check("Tell me sexual content")
    assert reason == "explicit"
    assert "can't help with sexual or explicit content" in message


def test_guardrail_blocks_non_education_query():
    reason, message = tutor._guardrail_check("Tell me celebrity gossip")
    assert reason == "off_topic"
    assert "education-related questions for standards 5-10" in message
    reason, _ = tutor._guardrail_check("Tell me my weaknesses in relationships")
    assert reason == "off_topic"


def test_guardrail_allows_followup_numeric_answer_when_last_turn_is_practice():
    turns = [
        {"role": "assistant", "content": "Now, test your understanding: simplify 2x^2 + 3x^2 and 7^2 * 7^4."}
    ]
    assert tutor._guardrail_check("1) 5x^2 2) 7^6", turns=turns) is None


def test_guardrail_still_blocks_answer_like_text_without_practice_context():
    reason, _ = tutor._guardrail_check("1) 5x^2 2) 7^6", turns=[{"role": "assistant", "content": "How are you today?"}])
    assert reason == "off_topic"
