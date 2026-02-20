# app/services/vertex_ai.py
# Vertex AI / Gemini service wrapper
#
# Two functions used across components:
#   1. generate_notes()    -- Component 11 (AI Notes)
#   2. generate_embedding() -- Component 17 (Content Embeddings / RAG)
#
# Model: gemini-2.5-flash (fast, cost-effective for structured output)
# Embeddings: text-embedding-004 (768 dimensions, matches pgvector schema)

import json
import os
import re
from typing import Optional

from app.core.config import settings


def _get_credentials():
    """
    Get Google credentials from environment.
    Returns google.auth.credentials.Credentials object or None.
    """
    # Try GOOGLE_APPLICATION_CREDENTIALS_JSON first
    creds_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if creds_json_str:
        try:
            from google.oauth2.credentials import Credentials
            creds_dict = json.loads(creds_json_str)
            return Credentials(
                token=None,
                refresh_token=creds_dict.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_dict.get("client_id"),
                client_secret=creds_dict.get("client_secret"),
                quota_project_id=creds_dict.get("quota_project_id"),
            )
        except Exception as e:
            print(f"Failed to load credentials from JSON env var: {e}")
    
    # Try service account file
    if settings.google_service_account_key_path:
        try:
            from google.oauth2 import service_account
            return service_account.Credentials.from_service_account_file(
                settings.google_service_account_key_path
            )
        except Exception as e:
            print(f"Failed to load service account: {e}")
    
    # Fall back to Application Default Credentials
    try:
        import google.auth
        credentials, project = google.auth.default()
        return credentials
    except Exception:
        return None


def _get_vertex_client():
    """
    Initialize Vertex AI client with explicit credentials.
    Returns None in dev mode if GCP not configured.
    """
    if not settings.gcp_project_id:
        return None
    
    credentials = _get_credentials()
    if not credentials:
        return None
    
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel

        vertexai.init(
            project=settings.gcp_project_id,
            location=settings.vertex_ai_location,
            credentials=credentials,
        )
        return GenerativeModel(settings.gemini_model)
    except Exception as e:
        print(f"Vertex AI initialization failed: {e}")
        return None


# ── Notes Generation ──────────────────────────────────────────────────────────

NOTES_GENERATION_PROMPT = """You are an expert educational content creator for Indian school students aged 10-14.

Given the following class transcript, generate comprehensive study notes in JSON format.

TRANSCRIPT:
{transcript}

Generate a JSON object with exactly this structure:
{{
  "summary": "2-3 sentence summary of what was covered in this class",
  "key_points": [
    "Key point 1",
    "Key point 2",
    "Key point 3",
    "Key point 4",
    "Key point 5"
  ],
  "detailed_notes": "Full markdown notes covering all topics discussed. Use ## headings, bullet points, and bold for important terms. Include examples from the class.",
  "qa_pairs": [
    {{"question": "Question 1?", "answer": "Answer 1"}},
    {{"question": "Question 2?", "answer": "Answer 2"}},
    {{"question": "Question 3?", "answer": "Answer 3"}}
  ]
}}

Rules:
- summary: 2-3 sentences, simple language for 10-14 year olds
- key_points: 5-8 bullet points, most important concepts only
- detailed_notes: Comprehensive markdown, include all examples discussed
- qa_pairs: 3-5 Q&A pairs covering key concepts, suitable for self-testing
- Use simple, clear English appropriate for Indian school students
- Return ONLY the JSON object, no other text
"""


def generate_notes(transcript_text: str) -> Optional[dict]:
    """
    Generate structured study notes from a class transcript using Gemini.

    Returns dict with keys: summary, key_points, detailed_notes, qa_pairs
    Returns None if generation fails.
    """
    model = _get_vertex_client()

    if not model:
        # Dev mode -- return mock notes
        return _mock_notes(transcript_text)

    prompt = NOTES_GENERATION_PROMPT.format(transcript=transcript_text[:8000])

    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,
                "top_p": 0.8,
                "max_output_tokens": 4096,
            },
        )
        raw_text = response.text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        return json.loads(raw_text)

    except json.JSONDecodeError:
        print("Gemini returned invalid JSON for notes generation")
        return None
    except Exception as e:
        print(f"Gemini notes generation failed: {e}")
        return None


def get_usage_metadata(response) -> dict:
    """Extract token usage from Gemini response."""
    try:
        return {
            "prompt_tokens": response.usage_metadata.prompt_token_count,
            "output_tokens": response.usage_metadata.candidates_token_count,
        }
    except Exception:
        return {"prompt_tokens": 0, "output_tokens": 0}


# ── Embeddings ────────────────────────────────────────────────────────────────

def generate_embedding(text: str) -> Optional[list]:
    """
    Generate a 768-dimensional embedding for text using text-embedding-004.
    Used by Component 17 (Content Embeddings) for RAG.

    Returns list of 768 floats or None if generation fails.
    """
    if not settings.gcp_project_id:
        return None
    
    credentials = _get_credentials()
    if not credentials:
        return None
    
    try:
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        vertexai.init(
            project=settings.gcp_project_id,
            location=settings.vertex_ai_location,
            credentials=credentials,
        )
        model = TextEmbeddingModel.from_pretrained(settings.embedding_model)
        embeddings = model.get_embeddings([text])
        return embeddings[0].values

    except Exception as e:
        print(f"Embedding generation failed: {e}")
        return None


# ── Mock Data (Dev Mode) ──────────────────────────────────────────────────────

def _mock_notes(transcript_text: str) -> dict:
    """
    Mock notes for development when Vertex AI is not configured.
    Parses the transcript to generate somewhat relevant mock content.
    """
    lines = [l.strip() for l in transcript_text.split("\n") if l.strip()]
    title = lines[0] if lines else "Class Notes"

    return {
        "summary": (
            f"This class covered {title}. "
            "Students learned key concepts and practised with examples. "
            "The teacher explained the topic using real-world examples suitable for the age group."
        ),
        "key_points": [
            "Variables represent unknown values in algebra",
            "An algebraic expression contains numbers, variables, and operators",
            "Like terms can be combined to simplify expressions",
            "Coefficients are numbers multiplied with variables",
            "Substitution means replacing a variable with its value",
        ],
        "detailed_notes": (
            "## Introduction\n\n"
            "In this class, we explored the fundamentals of algebraic expressions.\n\n"
            "## Key Concepts\n\n"
            "**Variables** are letters (like x, y, z) that represent unknown numbers.\n\n"
            "**Constants** are fixed numbers that don't change.\n\n"
            "**Coefficients** are numbers multiplied with variables. "
            "In `3x`, the coefficient is 3.\n\n"
            "## Examples\n\n"
            "- `2x + 3` is an algebraic expression\n"
            "- If x = 5, then `2x + 3 = 2(5) + 3 = 13`\n\n"
            "## Practice\n\n"
            "Try simplifying: `3x + 2x + 5`\n"
            "Answer: `5x + 5`"
        ),
        "qa_pairs": [
            {
                "question": "What is a variable in algebra?",
                "answer": "A variable is a letter (like x, y, z) that represents an unknown number.",
            },
            {
                "question": "What is a coefficient?",
                "answer": "A coefficient is the number multiplied with a variable. In 3x, the coefficient is 3.",
            },
            {
                "question": "How do you evaluate an expression?",
                "answer": "Replace the variable with its given value and calculate. For example, if x=5, then 2x+3 = 2(5)+3 = 13.",
            },
        ],
    }