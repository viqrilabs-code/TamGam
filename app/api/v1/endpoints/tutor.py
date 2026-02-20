# app/api/v1/endpoints/tutor.py
# AI Tutor (Diya) endpoints with citation support

import logging
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
from app.services.gemini_key_manager import (
    generate_embedding_with_fallback,
    generate_with_fallback,
)

logger = logging.getLogger("tamgam.tutor")

router = APIRouter()

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

CONTEXT_PROMPT = """Here are relevant excerpts from class notes, textbooks, and transcripts to help answer the question:

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
    """Search content_embeddings with source metadata for citations."""
    try:
        embedding = generate_embedding_with_fallback(question)
        if not embedding:
            logger.warning("Could not generate embedding for question")
            return []

        embedding = embedding[:768]
        emb_str = "[" + ",".join(str(v) for v in embedding) + "]"

        if class_id:
            results = db.execute(text("""
                SELECT ce.chunk_text, ce.content_type, ce.chunk_index,
                       ce.ncert_grade, ce.ncert_chapter,
                       b.title AS book_title,
                       ce.subject,
                       ce.embedding <=> cast(:embedding as vector) AS distance
                FROM content_embeddings ce
                LEFT JOIN books b ON ce.book_id = b.id
                WHERE ce.embedding IS NOT NULL
                  AND (ce.class_id = cast(:class_id as uuid) OR ce.content_type IN ('ncert_book', 'book_chunk'))
                ORDER BY ce.embedding <=> cast(:embedding as vector)
                LIMIT :top_k
            """), {"class_id": str(class_id), "embedding": emb_str, "top_k": top_k})
        else:
            results = db.execute(text("""
                SELECT ce.chunk_text, ce.content_type, ce.chunk_index,
                       ce.ncert_grade, ce.ncert_chapter,
                       b.title AS book_title,
                       ce.subject,
                       ce.embedding <=> cast(:embedding as vector) AS distance
                FROM content_embeddings ce
                LEFT JOIN books b ON ce.book_id = b.id
                WHERE ce.embedding IS NOT NULL
                ORDER BY ce.embedding <=> cast(:embedding as vector)
                LIMIT :top_k
            """), {"embedding": emb_str, "top_k": top_k})

        chunks = []
        for r in results:
            if r.distance < 0.8:
                if r.content_type == "ncert_book" and r.ncert_chapter:
                    source_label = f"NCERT Class {r.ncert_grade} – {r.ncert_chapter}"
                elif r.content_type == "book_chunk" and r.book_title:
                    source_label = f"Book: {r.book_title}"
                elif r.content_type == "transcript_chunk":
                    source_label = "Class Transcript"
                elif r.content_type == "note_section":
                    source_label = "Class Notes"
                else:
                    source_label = r.subject or "Study Material"

                chunks.append({
                    "text": r.chunk_text,
                    "content_type": r.content_type,
                    "source": source_label,
                    "distance": round(r.distance, 4),
                })

        logger.info(f"RAG search returned {len(chunks)} chunks")
        return chunks

    except Exception as e:
        logger.error(f"RAG search failed: {e}")
        return []


def _call_gemini(question, context_chunks, history, level):
    """Call Gemini via key manager."""
    try:
        system_prompt = DIYA_SYSTEM_PROMPTS.get(level, DIYA_SYSTEM_PROMPTS[3])

        context_str = ""
        if context_chunks:
            context_str = "\n\n---\n\n".join(c["text"] for c in context_chunks if c.get("text"))

        history_text = ""
        if history:
            for turn in history[-6:]:
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

        answer = generate_with_fallback(full_prompt, model_name="gemini-2.0-flash")
        logger.info(f"Diya answered (level={level}, context_chunks={len(context_chunks)})")
        return answer

    except Exception as e:
        logger.error(f"Gemini call failed: {e}")
        return "I'm sorry, I'm having trouble thinking right now. Please try again in a moment! 🪔"


def _extract_sources(context_chunks):
    """Extract unique sources for citation display."""
    seen = set()
    sources = []
    for chunk in context_chunks:
        label = chunk.get("source", "")
        if label and label not in seen:
            seen.add(label)
            sources.append({
                "label": label,
                "type": chunk.get("content_type", ""),
            })
    return sources


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
    if not _is_subscribed(current_user.id, db):
        raise HTTPException(
            status_code=403,
            detail={"message": "Active subscription required to use Diya.", "redirect": "/pricing"},
        )

    session = None
    if payload.session_id:
        session = db.query(TutorSession).filter(
            and_(TutorSession.id == payload.session_id, TutorSession.user_id == current_user.id)
        ).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found.")

    if not session:
        subject = None
        if payload.class_id:
            cls = db.query(Class).filter(Class.id == payload.class_id).first()
            subject = cls.subject if cls else None
        session = TutorSession(user_id=current_user.id, class_id=payload.class_id, subject=subject, turns=[])
        db.add(session)
        db.flush()

    subject = session.subject or "General"
    level = _get_understanding_level(current_user.id, subject, db)

    context_chunks = _search_relevant_chunks(payload.question, payload.class_id or session.class_id, db)
    history = list(session.turns or [])
    answer = _call_gemini(payload.question, context_chunks, history, level)
    sources = _extract_sources(context_chunks)

    now = datetime.now(timezone.utc).isoformat()
    new_turns = list(history) + [
        {"role": "user", "content": payload.question, "timestamp": now},
        {"role": "assistant", "content": answer, "timestamp": now, "sources_used": len(context_chunks), "sources": sources},
    ]
    session.turns = new_turns
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    return TutorAskResponse(
        session_id=session.id,
        answer=answer,
        sources_used=len(context_chunks),
        understanding_level=level,
        sources=sources,
    )


@router.get("/sessions", response_model=List[TutorSessionSummary], summary="List own tutor sessions")
def list_sessions(current_user: User = Depends(require_login), db: Session = Depends(get_db)):
    sessions = db.query(TutorSession, Class).outerjoin(
        Class, Class.id == TutorSession.class_id
    ).filter(TutorSession.user_id == current_user.id).order_by(TutorSession.updated_at.desc()).all()

    return [
        TutorSessionSummary(
            id=s.id, class_id=s.class_id, class_title=c.title if c else None,
            message_count=len(s.turns or []), last_message_at=s.updated_at, created_at=s.created_at,
        )
        for s, c in sessions
    ]


@router.get("/sessions/{session_id}", response_model=TutorSessionDetail, summary="Get full session history")
def get_session(session_id: UUID, current_user: User = Depends(require_login), db: Session = Depends(get_db)):
    result = db.query(TutorSession, Class).outerjoin(
        Class, Class.id == TutorSession.class_id
    ).filter(and_(TutorSession.id == session_id, TutorSession.user_id == current_user.id)).first()

    if not result:
        raise HTTPException(status_code=404, detail="Session not found.")

    session, cls = result
    messages = [
        TutorMessage(role=t["role"], content=t["content"], timestamp=t.get("timestamp"), sources=t.get("sources", []))
        for t in (session.turns or [])
    ]

    return TutorSessionDetail(
        id=session.id, class_id=session.class_id, class_title=cls.title if cls else None,
        messages=messages, created_at=session.created_at, updated_at=session.updated_at,
    )