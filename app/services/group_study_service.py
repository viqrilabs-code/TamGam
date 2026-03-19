import base64
import hashlib
import json
import random
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

from cryptography.fernet import Fernet

from app.core.config import settings
from app.services.gemini_key_manager import GeminiQuotaExhausted, generate_with_api_key

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has",
    "have", "how", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the",
    "their", "there", "these", "this", "to", "was", "were", "what", "when", "where",
    "which", "who", "why", "with", "you", "your", "they", "them", "will", "can",
}

STEM_SUBJECTS = {
    "mathematics",
    "maths",
    "math",
    "science",
    "physics",
    "chemistry",
    "biology",
}
ENGLISH_SUBJECTS = {"english", "literature", "language", "grammar"}
DIFFICULTY_LEVELS = {"easy", "medium", "hard"}


def normalize_subject_bucket(subject: str) -> str:
    value = (subject or "").strip().lower()
    if value in STEM_SUBJECTS:
        return "stem"
    if value in ENGLISH_SUBJECTS:
        return "english"
    return "general"


def normalize_difficulty_level(value: Any) -> str:
    difficulty = str(value or "").strip().lower()
    return difficulty if difficulty in DIFFICULTY_LEVELS else "medium"


def infer_section_difficulty(section: dict[str, Any], subject_bucket: str) -> str:
    text = str(section.get("text") or "")
    word_count = int(section.get("word_count") or len(re.findall(r"[A-Za-z0-9']+", text)))
    lower = text.lower()
    hard_markers = ("derive", "justify", "compare", "analyse", "analyze", "evaluate", "proof", "experiment")
    medium_markers = ("explain", "reason", "solve", "interpret", "evidence", "example")

    if word_count >= 220 or any(marker in lower for marker in hard_markers):
        return "hard"
    if subject_bucket == "english" and word_count >= 140:
        return "hard"
    if word_count >= 110 or any(marker in lower for marker in medium_markers):
        return "medium"
    if subject_bucket == "english" and word_count >= 70:
        return "medium"
    return "easy"


def resolve_turn_time_limit_seconds(subject_bucket: str, difficulty_level: str, *, discussion_prompt: bool = False) -> Optional[int]:
    if discussion_prompt:
        return None
    difficulty = normalize_difficulty_level(difficulty_level)
    if subject_bucket == "english":
        return {"easy": 20, "medium": 25, "hard": 30}[difficulty]
    if subject_bucket == "stem":
        return {"easy": 10, "medium": 20, "hard": 30}[difficulty]
    return {"easy": 15, "medium": 20, "hard": 30}[difficulty]


def _section_title(seed_text: str, fallback_index: int) -> str:
    first_line = (seed_text or "").strip().splitlines()[0].strip() if seed_text else ""
    if first_line and len(first_line) <= 80 and not first_line.endswith("."):
        return first_line[:80]
    words = re.findall(r"[A-Za-z0-9']+", seed_text or "")
    title = " ".join(words[:8]).strip()
    return title[:80] if title else f"Section {fallback_index + 1}"


def sectionize_content(
    *,
    title: str,
    source_text: str,
    topic_outline: Optional[str] = None,
    max_sections: int = 6,
    target_chars: int = 1200,
) -> list[dict[str, Any]]:
    raw = (source_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw and topic_outline:
        raw = topic_outline.strip()
    if not raw:
        return [{"index": 0, "title": title.strip() or "Discussion", "text": "Open discussion", "word_count": 2}]

    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw) if b and b.strip()]
    if not blocks:
        blocks = [line.strip() for line in raw.splitlines() if line.strip()]

    sections: list[str] = []
    buffer = ""
    for block in blocks:
        candidate = f"{buffer}\n\n{block}".strip() if buffer else block
        if buffer and len(candidate) > target_chars and len(sections) < max_sections - 1:
            sections.append(buffer.strip())
            buffer = block
        else:
            buffer = candidate
    if buffer:
        sections.append(buffer.strip())

    if len(sections) > max_sections:
        merged: list[str] = []
        chunk_size = max(1, round(len(sections) / max_sections))
        for start in range(0, len(sections), chunk_size):
            merged.append("\n\n".join(sections[start:start + chunk_size]).strip())
        sections = merged[:max_sections]

    payload = []
    for idx, text in enumerate(sections[:max_sections]):
        payload.append(
            {
                "index": idx,
                "title": _section_title(text, idx),
                "text": text[:5000],
                "word_count": len(re.findall(r"[A-Za-z0-9']+", text)),
            }
        )
    return payload or [{"index": 0, "title": title.strip() or "Discussion", "text": raw[:5000], "word_count": len(re.findall(r"[A-Za-z0-9']+", raw))}]


def normalize_gemini_key(value: str) -> str:
    key = (value or "").strip()
    if len(key) < 20 or len(key) > 200:
        raise ValueError("Invalid Gemini API key format.")
    return key


def _fernet() -> Fernet:
    secret = (settings.jwt_secret_key or "tamgam-group-study-secret").encode("utf-8")
    digest = hashlib.sha256(secret).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_gemini_key(value: str) -> str:
    return _fernet().encrypt(normalize_gemini_key(value).encode("utf-8")).decode("utf-8")


def decrypt_gemini_key(value: str) -> str:
    return _fernet().decrypt((value or "").encode("utf-8")).decode("utf-8")


def execute_with_group_study_keys(
    api_keys: Sequence[str],
    operation: Callable[[str], Any],
) -> Any:
    exhausted = 0
    last_quota_error: Optional[Exception] = None
    for raw_key in api_keys:
        key = (raw_key or "").strip()
        if not key:
            continue
        try:
            return operation(key)
        except GeminiQuotaExhausted as exc:
            exhausted += 1
            last_quota_error = exc
            continue
    if exhausted:
        raise GeminiQuotaExhausted("All submitted Gemini API keys are exhausted.") from last_quota_error
    raise GeminiQuotaExhausted("No submitted Gemini API keys are available for this group study.")


def _extract_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty model response")
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("invalid json object")


def _keywords(text: str, limit: int = 6) -> list[str]:
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9']{2,}", text or "")]
    counts = Counter(w for w in words if w not in STOPWORDS)
    return [word for word, _ in counts.most_common(limit)]


def _stable_random_choice(items: Sequence[dict], seed_text: str) -> Optional[dict]:
    if not items:
        return None
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return items[rng.randrange(0, len(items))]


def choose_target_participant(
    participants: Sequence[dict[str, Any]],
    *,
    study_id: str,
    turn_index: int,
) -> Optional[dict[str, Any]]:
    if not participants:
        return None
    ordered = sorted(
        participants,
        key=lambda item: (
            int(item.get("participation_count") or 0),
            int(item.get("correct_answers") or 0),
            str(item.get("user_id") or ""),
        ),
    )
    lowest_participation = int(ordered[0].get("participation_count") or 0)
    pool = [item for item in ordered if int(item.get("participation_count") or 0) == lowest_participation]
    return _stable_random_choice(pool, f"{study_id}:{turn_index}")


def _clip_sentences(text: str, max_sentences: int = 2) -> str:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text or "") if p and p.strip()]
    if parts:
        return " ".join(parts[:max_sentences])[:500]
    return (text or "").strip()[:500]


def _fallback_turn_payload(
    *,
    subject: str,
    subject_bucket: str,
    section: dict[str, Any],
    target_name: Optional[str],
    discussion_prompt: bool,
) -> dict[str, Any]:
    explanation = _clip_sentences(section.get("text", ""), max_sentences=3) or section.get("title") or "Let us discuss this topic carefully."
    title = section.get("title") or "this section"
    keywords = _keywords(section.get("text", ""), limit=4)
    difficulty_level = infer_section_difficulty(section, subject_bucket)
    time_limit_seconds = resolve_turn_time_limit_seconds(subject_bucket, difficulty_level, discussion_prompt=discussion_prompt)
    if discussion_prompt:
        return {
            "turn_type": "discussion_prompt",
            "prompt_text": f"Diya is moderating the group discussion on {title}. Everyone should contribute one clear point and one example.",
            "question_text": f"Discussion topic: What is the most important idea from {title}, and how can the group apply it?",
            "options": [],
            "correct_answer": None,
            "expected_points": keywords,
            "difficulty_level": None,
            "time_limit_seconds": None,
        }
    if subject_bucket == "english":
        prompt = f"{explanation}\n\n{target_name or 'A student'}, answer in your own words with a clear explanation."
        return {
            "turn_type": "subjective_question",
            "prompt_text": prompt,
            "question_text": f"What is the main idea of {title}, and which detail from the text supports it?",
            "options": [],
            "correct_answer": None,
            "expected_points": keywords,
            "difficulty_level": difficulty_level,
            "time_limit_seconds": time_limit_seconds,
        }
    return {
        "turn_type": "mcq_question",
        "prompt_text": f"{explanation}\n\n{target_name or 'A student'}, read the highlighted idea and then solve the question.",
        "question_text": f"Which option best matches the main idea of {title}?",
        "options": [
            {"key": "A", "text": title},
            {"key": "B", "text": "It says the topic has no clear rule or process."},
            {"key": "C", "text": "It focuses only on memorizing unrelated facts."},
            {"key": "D", "text": "It avoids explaining the concept entirely."},
        ],
        "correct_answer": "A",
        "expected_points": keywords,
        "difficulty_level": difficulty_level,
        "time_limit_seconds": time_limit_seconds,
    }


def _mcq_generation_prompt(subject: str, section: dict[str, Any], target_name: str, participant_names: str) -> str:
    return f"""
You are Diya guiding a live group study on tamgam for Indian school students.

Subject: {subject}
Students in the room: {participant_names}
Target student for this turn: {target_name}
Section title: {section.get("title") or "Discussion"}
Section text:
{section.get("text") or ""}

Return strict JSON only:
{{
  "explanation": "2-4 sentence explanation of the section in simple classroom language",
  "read_prompt": "one sentence asking {target_name} to read or summarize the section",
  "question": "one MCQ question from this section",
  "options": [
    {{"key": "A", "text": "option text"}},
    {{"key": "B", "text": "option text"}},
    {{"key": "C", "text": "option text"}},
    {{"key": "D", "text": "option text"}}
  ],
  "correct_answer": "A/B/C/D",
  "rationale": "1 sentence explanation for the correct answer",
  "difficulty_level": "easy/medium/hard"
}}
No markdown.
    """.strip()


def _english_generation_prompt(subject: str, section: dict[str, Any], target_name: str, participant_names: str) -> str:
    return f"""
You are Diya guiding a live English group study on tamgam for Indian school students.

Subject: {subject}
Students in the room: {participant_names}
Target student for this turn: {target_name}
Section title: {section.get("title") or "Discussion"}
Section text:
{section.get("text") or ""}

Return strict JSON only:
{{
  "explanation": "2-4 sentence explanation of the section",
  "question": "one subjective question that needs interpretation or explanation",
  "expected_points": ["point 1", "point 2", "point 3"],
  "moderator_tip": "one short line telling {target_name} to answer clearly in their own words",
  "difficulty_level": "easy/medium/hard"
}}
No markdown.
    """.strip()


def _discussion_generation_prompt(subject: str, section: dict[str, Any], participant_names: str) -> str:
    return f"""
You are Diya moderating a live group study discussion on tamgam.

Subject: {subject}
Students in the room: {participant_names}
Section title: {section.get("title") or "Discussion"}
Section text:
{section.get("text") or ""}

Return strict JSON only:
{{
  "prompt_text": "2-3 sentence moderation instruction for the group",
  "question": "one open discussion prompt for the whole group",
  "expected_points": ["point 1", "point 2", "point 3"]
}}
No markdown.
    """.strip()


def _normalize_generated_payload(
    *,
    raw_payload: dict[str, Any],
    fallback: dict[str, Any],
    subject_bucket: str,
    discussion_prompt: bool,
) -> dict[str, Any]:
    if discussion_prompt:
        prompt_text = str(raw_payload.get("prompt_text") or fallback["prompt_text"]).strip()
        question_text = str(raw_payload.get("question") or fallback["question_text"]).strip()
        expected_points = [str(item).strip() for item in (raw_payload.get("expected_points") or fallback.get("expected_points") or []) if str(item).strip()]
        return {
            "turn_type": "discussion_prompt",
            "prompt_text": prompt_text,
            "question_text": question_text,
            "options": [],
            "correct_answer": None,
            "expected_points": expected_points,
            "difficulty_level": None,
            "time_limit_seconds": None,
        }

    if fallback["turn_type"] == "subjective_question":
        explanation = str(raw_payload.get("explanation") or "").strip()
        moderator_tip = str(raw_payload.get("moderator_tip") or "").strip()
        question = str(raw_payload.get("question") or "").strip()
        expected_points = [str(item).strip() for item in (raw_payload.get("expected_points") or []) if str(item).strip()]
        difficulty_level = normalize_difficulty_level(raw_payload.get("difficulty_level") or fallback.get("difficulty_level"))
        return {
            "turn_type": "subjective_question",
            "prompt_text": (f"{explanation}\n\n{moderator_tip}".strip() or fallback["prompt_text"]),
            "question_text": question or fallback["question_text"],
            "options": [],
            "correct_answer": None,
            "expected_points": expected_points or fallback.get("expected_points") or [],
            "difficulty_level": difficulty_level,
            "time_limit_seconds": resolve_turn_time_limit_seconds(subject_bucket, difficulty_level),
        }

    explanation = str(raw_payload.get("explanation") or "").strip()
    read_prompt = str(raw_payload.get("read_prompt") or "").strip()
    question = str(raw_payload.get("question") or "").strip()
    correct_answer = str(raw_payload.get("correct_answer") or "").strip().upper()
    difficulty_level = normalize_difficulty_level(raw_payload.get("difficulty_level") or fallback.get("difficulty_level"))
    raw_options = raw_payload.get("options") or []
    options: list[dict[str, str]] = []
    for item in raw_options:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().upper()
        text = str(item.get("text") or "").strip()
        if key in {"A", "B", "C", "D"} and text:
            options.append({"key": key, "text": text})
    if len(options) != 4 or correct_answer not in {"A", "B", "C", "D"}:
        return fallback
    return {
        "turn_type": "mcq_question",
        "prompt_text": (f"{explanation}\n\n{read_prompt}".strip() or fallback["prompt_text"]),
        "question_text": question or fallback["question_text"],
        "options": options,
        "correct_answer": correct_answer,
        "expected_points": fallback.get("expected_points") or [],
        "difficulty_level": difficulty_level,
        "time_limit_seconds": resolve_turn_time_limit_seconds(subject_bucket, difficulty_level),
    }


def generate_turn_payload(
    *,
    study_id: str,
    turn_index: int,
    subject: str,
    section: dict[str, Any],
    participants: Sequence[dict[str, Any]],
    api_keys: Sequence[str],
    discussion_prompt: bool = False,
) -> dict[str, Any]:
    subject_bucket = normalize_subject_bucket(subject)
    target = None if discussion_prompt else choose_target_participant(participants, study_id=study_id, turn_index=turn_index)
    target_name = str((target or {}).get("full_name") or "Student").strip()
    participant_names = ", ".join(str(p.get("full_name") or "Student").strip() for p in participants) or "Students"
    fallback = _fallback_turn_payload(
        subject=subject,
        subject_bucket=subject_bucket,
        section=section,
        target_name=target_name,
        discussion_prompt=discussion_prompt,
    )

    if discussion_prompt:
        prompt = _discussion_generation_prompt(subject, section, participant_names)
    elif subject_bucket == "english":
        prompt = _english_generation_prompt(subject, section, target_name, participant_names)
    else:
        prompt = _mcq_generation_prompt(subject, section, target_name, participant_names)

    try:
        raw = execute_with_group_study_keys(
            api_keys,
            lambda key: generate_with_api_key(prompt=prompt, api_key=key, model_name="gemini-2.0-flash"),
        )
        parsed = _extract_json_object(raw or "")
        payload = _normalize_generated_payload(
            raw_payload=parsed,
            fallback=fallback,
            subject_bucket=subject_bucket,
            discussion_prompt=discussion_prompt,
        )
    except GeminiQuotaExhausted:
        raise
    except Exception:
        payload = fallback

    payload["target_user_id"] = target.get("user_id") if target else None
    payload["target_name"] = target_name if target else None
    payload["section_title"] = section.get("title")
    payload["source_excerpt"] = (section.get("text") or "")[:1400]
    return payload


def evaluate_subjective_answer(answer_text: str, source_excerpt: str, question_text: str) -> dict[str, Any]:
    answer = (answer_text or "").strip()
    if not answer:
        return {
            "score": 0.0,
            "strengths": [],
            "improvement_areas": ["Respond to the question in complete sentences."],
            "feedback": "No answer was submitted.",
        }
    answer_words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9']{2,}", answer)}
    expected = _keywords(f"{source_excerpt} {question_text}", limit=6)
    overlap = [word for word in expected if word in answer_words]
    missing = [word for word in expected if word not in answer_words]
    length_bonus = 1 if len(answer_words) >= 18 or len(answer) >= 120 else 0
    score = float(min(5, max(1, len(overlap) + length_bonus)))
    strengths = []
    if overlap:
        strengths.append(f"Used key ideas such as {', '.join(overlap[:3])}.")
    if len(answer) >= 80:
        strengths.append("Explained the answer with usable detail.")
    improvements = []
    if missing:
        improvements.append(f"Include points about {', '.join(missing[:3])}.")
    if len(answer) < 50:
        improvements.append("Add a fuller explanation with one supporting detail.")
    feedback = strengths[0] if strengths else "You attempted the answer."
    return {
        "score": score,
        "strengths": strengths,
        "improvement_areas": improvements,
        "feedback": feedback,
    }


def evaluate_group_study_answer(
    *,
    turn_type: str,
    answer_text: Optional[str],
    answer_choice: Optional[str],
    correct_answer: Optional[str],
    source_excerpt: str,
    question_text: str,
) -> dict[str, Any]:
    if turn_type == "mcq_question":
        choice = (answer_choice or "").strip().upper()
        is_correct = bool(choice and correct_answer and choice == correct_answer.strip().upper())
        return {
            "score": 1.0 if is_correct else 0.0,
            "is_correct": is_correct,
            "strengths": ["Answered the MCQ correctly."] if is_correct else [],
            "improvement_areas": [] if is_correct else ["Review this section and check why the selected option was not the best answer."],
            "feedback": "Correct answer." if is_correct else "Incorrect answer. Review the concept and try again.",
        }
    if turn_type == "discussion_prompt":
        answer = (answer_text or "").strip()
        enough_detail = len(answer) >= 40
        return {
            "score": 1.0 if enough_detail else 0.5,
            "is_correct": None,
            "strengths": ["Shared a group discussion point."] if answer else [],
            "improvement_areas": [] if enough_detail else ["Add a clearer example or supporting reason in the discussion."],
            "feedback": "Good participation." if enough_detail else "Participation recorded.",
        }
    subjective = evaluate_subjective_answer(answer_text or "", source_excerpt, question_text)
    subjective["is_correct"] = None
    return subjective


def build_group_study_report(
    *,
    title: str,
    participants: Sequence[dict[str, Any]],
    turns: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    participant_map = {str(item["user_id"]): dict(item) for item in participants}
    strength_map: dict[str, list[str]] = {key: [] for key in participant_map}
    improvement_map: dict[str, list[str]] = {key: [] for key in participant_map}

    for turn in turns:
        target_id = str(turn.get("target_user_id") or "")
        if not target_id or target_id not in participant_map:
            continue
        evaluation = turn.get("evaluation_data") or {}
        for strength in evaluation.get("strengths") or []:
            value = str(strength).strip()
            if value and value not in strength_map[target_id]:
                strength_map[target_id].append(value)
        for area in evaluation.get("improvement_areas") or []:
            value = str(area).strip()
            if value and value not in improvement_map[target_id]:
                improvement_map[target_id].append(value)

    ranked = sorted(
        participant_map.values(),
        key=lambda item: (
            float(item.get("total_score") or 0.0),
            int(item.get("correct_answers") or 0),
            int(item.get("participation_count") or 0),
        ),
        reverse=True,
    )
    has_meaningful_activity = any(
        float(item.get("total_score") or 0.0) > 0
        or int(item.get("total_questions") or 0) > 0
        or int(item.get("participation_count") or 0) > 0
        or int(item.get("correct_answers") or 0) > 0
        for item in ranked
    )
    winner = ranked[0] if ranked and has_meaningful_activity else None
    report_participants = []
    for row in ranked:
        key = str(row["user_id"])
        report_participants.append(
            {
                "user_id": key,
                "full_name": row["full_name"],
                "score": round(float(row.get("total_score") or 0.0), 2),
                "total_questions": int(row.get("total_questions") or 0),
                "correct_answers": int(row.get("correct_answers") or 0),
                "participation_count": int(row.get("participation_count") or 0),
                "strengths": strength_map.get(key, [])[:3],
                "improvement_areas": improvement_map.get(key, [])[:3],
            }
        )

    winner_name = winner.get("full_name") if winner else None
    summary = (
        f"{winner_name} led the group study with strong participation and the highest score."
        if winner_name
        else "The group study ended without enough responses to declare a winner."
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "winner_user_id": str(winner["user_id"]) if winner else None,
        "winner_name": winner_name,
        "summary": summary,
        "share_caption": (
            f"Winner of '{title}' group study on tamgam.in: {winner_name}."
            if winner_name
            else f"Group study '{title}' completed on tamgam.in."
        ),
        "participants": report_participants,
    }
