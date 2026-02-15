# app/api/v1/endpoints/tutor.py
# AI Tutor (Diya) endpoints
#
# RAG flow per question:
#   1. Get student's understanding level (1-5) for calibration
#   2. Embed the question using text-embedding-004
#   3. Search content_embeddings via pgvector cosine similarity
#   4. Build Gemini prompt: system + context chunks + conversation history + question
#   5. Stream response, store turn in tutor_sessions.turns
#
# In dev mode (no Vertex AI): returns a helpful mock response

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, text
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login
from app.db.session import get_db
from app.models.ai import ContentEmbedding, TutorSession
from app.models.assessment import StudentUnderstandingProfile
from app.models.class_ import Class
from app.models.student import StudentProfile
from app.models.subscription import Subscription
from app.models.user import User
from app.schemas.tutor import (
    MessageResponse,
    TutorAskRequest,
    TutorAskResponse,
    TutorMessage,
    TutorSessionDetail,
    TutorSessionSummary,
)

router = APIRouter()

# Diya's persona calibrated by understanding level
DIYA_SYSTEM_PROMPTS = {
    1: """You are Diya, a warm and patient AI tutor for Indian school students.
This student is a beginner. Use very simple language, real-world examples from everyday Indian life,
and break everything into tiny steps. Encourage them often. Use analogies like cricket, food, or festivals.""",

    2: """You are Diya, a friendly AI tutor for Indian school students.
This student is developing their understanding. Use simple language with worked examples.
Give gentle hints before full explanations. Connect concepts to things they already know.""",

    3: """You are Diya, a helpful AI tutor for Indian school students.
This student has standard understanding. Give clear, structured explanations with examples.
Balance conceptual depth with accessibility. Encourage curiosity.""",

    4: """You are Diya, an AI tutor for advanced Indian school students.
This student is advanced. Be concise and precise. Include deeper patterns and competitive exam angles.
Challenge them with follow-up questions.""",

    5: """You are Diya, an AI tutor for highly advanced Indian school students.
This student is at expert level. Engage peer-to-peer. Discuss edge cases, proofs, and research angles.
Ask probing questions to deepen their thinking.""",
}

CONTEXT_PROMPT = """Here are relevant excerpts from class notes and transcripts to help answer the question:

{context}

Based on the above context and the conversation history, answer the student's question.
If the context doesn't contain enough information, answer from your general knowledge but say so.
Keep your answer focused and appropriate for the student's level.
Always respond in English."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_subscribed(user_id, db):
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _get_understanding_level(user_id, subject, db) -> int:
    sp = db.query(StudentProfile).filter(StudentProfile.user_id == user_id).first()
    if not sp:
        return 3
    profile = db.query(StudentUnderstandingProfile).filter(
        and_(
            StudentUnderstandingProfile.student_id == sp.id,
            StudentUnderstandingProfile.subject == subject,
        )
    ).first()
    return profile.current_level if profile else 3


def _search_relevant_chunks(question: str, class_id: Optional[UUID], db, top_k: int = 5) -> List[dict]:
    """
    Search content_embeddings using pgvector cosine similarity.
    In dev mode (no embeddings stored), returns empty list.
    """
    try:
        from app.services.vertex_ai import generate_embedding
        embedding = generate_embedding(question)
        if not embedding:
            return []

        # pgvector cosine similarity search
        if class_id:
            results = db.execute(text("""
                SELECT chunk_text, source_type, chunk_index
                FROM content_embeddings
                WHERE class_id = :class_id
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :top_k
            """), {"class_id": str(class_id), "embedding": str(embedding), "top_k": top_k})
        else:
            results = db.execute(text("""
                SELECT chunk_text, source_type, chunk_index
                FROM content_embeddings
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :top_k
            """), {"embedding": str(embedding), "top_k": top_k})

        return [{"text": r[0], "source_type": r[1]} for r in results if r[0]]
    except Exception:
        return []


def _call_gemini(
    question: str,
    context_chunks: List[dict],
    history: List[dict],
    level: int,
) -> str:
    """
    Call Gemini with system prompt + context + history + question.
    Returns answer string. Falls back to mock if Gemini unavailable.
    """
    try:
        from app.services.vertex_ai import _get_vertex_client
        model = _get_vertex_client()
        if not model:
            return _mock_answer(question, level)

        system_prompt = DIYA_SYSTEM_PROMPTS.get(level, DIYA_SYSTEM_PROMPTS[3])

        # Build context string
        context_str = ""
        if context_chunks:
            context_str = "\n\n---\n\n".join(c["text"] for c in context_chunks if c.get("text"))

        # Build conversation history for Gemini
        history_text = ""
        if history:
            for turn in history[-6:]:  # Last 3 exchanges = 6 turns
                role = "Student" if turn["role"] == "user" else "Diya"
                history_text += f"{role}: {turn['content']}\n\n"

        if context_str:
            full_prompt = (
                f"{system_prompt}\n\n"
                f"{CONTEXT_PROMPT.format(context=context_str)}\n\n"
                f"{'Conversation history:' + chr(10) + history_text if history_text else ''}"
                f"Student: {question}\n\nDiya:"
            )
        else:
            full_prompt = (
                f"{system_prompt}\n\n"
                f"{'Conversation history:' + chr(10) + history_text if history_text else ''}"
                f"Student: {question}\n\nDiya:"
            )

        response = model.generate_content(
            full_prompt,
            generation_config={"temperature": 0.7, "max_output_tokens": 1024},
        )
        return response.text.strip()

    except Exception as e:
        print(f"Gemini tutor call failed: {e}")
        return _mock_answer(question, level)


def _mock_answer(question: str, level: int) -> str:
    """Mock answer for dev mode."""
    level_note = {
        1: "Let me explain this very simply for you! ",
        2: "Great question! Let me walk you through this step by step. ",
        3: "Good question! Here's a clear explanation. ",
        4: "Excellent question. Here's a precise answer. ",
        5: "Interesting question. Let's explore this deeply. ",
    }.get(level, "")

    return (
        f"{level_note}This is a mock response from Diya (Vertex AI not configured). "
        f"You asked: '{question[:100]}'. "
        f"In production, Diya would search your class notes and transcripts using RAG "
        f"and provide a personalised answer calibrated to your understanding level ({level}/5). "
        f"Configure GCP_PROJECT_ID and GOOGLE_SERVICE_ACCOUNT_KEY_PATH in .env to enable real AI responses."
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/ask",
    response_model=TutorAskResponse,
    summary="Ask Diya a question (subscription required)",
)
def ask_diya(
    payload: TutorAskRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Ask the AI Tutor (Diya) a question.
    Requires active subscription.
    Continues an existing session or starts a new one.
    Answer is grounded in class notes/transcripts via RAG when available.
    Response style calibrated to student's understanding level.
    """
    if not _is_subscribed(current_user.id, db):
        raise HTTPException(
            status_code=403,
            detail={"message": "Active subscription required to use Diya.", "redirect": "/pricing"},
        )

    # Resolve or create session
    session = None
    if payload.session_id:
        session = db.query(TutorSession).filter(
            and_(
                TutorSession.id == payload.session_id,
                TutorSession.user_id == current_user.id,
            )
        ).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found.")

    if not session:
        # Determine subject from class if provided
        subject = None
        if payload.class_id:
            cls = db.query(Class).filter(Class.id == payload.class_id).first()
            subject = cls.subject if cls else None

        session = TutorSession(
            user_id=current_user.id,
            class_id=payload.class_id,
            subject=subject,
            turns=[],
        )
        db.add(session)
        db.flush()

    # Get student's understanding level
    subject = session.subject or "General"
    level = _get_understanding_level(current_user.id, subject, db)

    # RAG: search relevant content
    context_chunks = _search_relevant_chunks(
        payload.question,
        payload.class_id or session.class_id,
        db,
    )

    # Get conversation history
    history = list(session.turns or [])

    # Call Gemini
    answer = _call_gemini(payload.question, context_chunks, history, level)

    # Append turns to session
    now = datetime.now(timezone.utc).isoformat()
    new_turns = list(history) + [
        {"role": "user", "content": payload.question, "timestamp": now},
        {"role": "assistant", "content": answer, "timestamp": now, "sources_used": len(context_chunks)},
    ]
    session.turns = new_turns
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    return TutorAskResponse(
        session_id=session.id,
        answer=answer,
        sources_used=len(context_chunks),
        understanding_level=level,
    )


@router.get(
    "/sessions",
    response_model=List[TutorSessionSummary],
    summary="List own tutor sessions",
)
def list_sessions(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """List all tutor sessions for the current user, newest first."""
    sessions = db.query(TutorSession, Class).outerjoin(
        Class, Class.id == TutorSession.class_id
    ).filter(
        TutorSession.user_id == current_user.id
    ).order_by(TutorSession.updated_at.desc()).all()

    return [
        TutorSessionSummary(
            id=session.id,
            class_id=session.class_id,
            class_title=cls.title if cls else None,
            message_count=len(session.turns or []),
            last_message_at=session.updated_at,
            created_at=session.created_at,
        )
        for session, cls in sessions
    ]


@router.get(
    "/sessions/{session_id}",
    response_model=TutorSessionDetail,
    summary="Get full session history",
)
def get_session(
    session_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Get full conversation history for a tutor session."""
    result = db.query(TutorSession, Class).outerjoin(
        Class, Class.id == TutorSession.class_id
    ).filter(
        and_(
            TutorSession.id == session_id,
            TutorSession.user_id == current_user.id,
        )
    ).first()

    if not result:
        raise HTTPException(status_code=404, detail="Session not found.")

    session, cls = result
    messages = [
        TutorMessage(
            role=t["role"],
            content=t["content"],
            timestamp=t.get("timestamp"),
        )
        for t in (session.turns or [])
    ]

    return TutorSessionDetail(
        id=session.id,
        class_id=session.class_id,
        class_title=cls.title if cls else None,
        messages=messages,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )