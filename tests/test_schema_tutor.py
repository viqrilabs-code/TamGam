from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.tutor import TutorAskRequest


def test_tutor_ask_request_valid():
    req = TutorAskRequest(question="Explain fractions", session_id=uuid4(), class_id=uuid4(), gemini_api_key="a" * 30)
    assert req.question == "Explain fractions"
    assert req.gemini_api_key == "a" * 30


def test_tutor_ask_request_question_validator():
    with pytest.raises(ValidationError):
        TutorAskRequest(question="   ")

    with pytest.raises(ValidationError):
        TutorAskRequest(question="x" * 2001)


def test_tutor_ask_request_api_key_validator():
    req = TutorAskRequest(question="ok", gemini_api_key="   ")
    assert req.gemini_api_key is None

    with pytest.raises(ValidationError):
        TutorAskRequest(question="ok", gemini_api_key="short")

