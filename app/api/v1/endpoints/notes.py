# app/api/v1/endpoints/notes.py
# AI Notes generation and management endpoints
#
# Flow:
#   1. Teacher triggers generation after transcript is ready
#   2. Gemini generates structured notes in background
#   3. Teacher reviews (approve/reject)
#   4. Approved notes visible to subscribed students

import json
import logging
import base64
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, File, Form, UploadFile
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login, require_teacher
from app.db.session import get_db
from app.models.assessment import StudentUnderstandingProfile
from app.models.class_ import Class
from app.models.note import Note
from app.models.student import StudentProfile
from app.models.student_note_request import StudentNoteRequest
from app.models.subscription import Subscription
from app.models.teacher import TeacherProfile
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.note import (
    NoteContent,
    NoteEditRequest,
    NoteResponse,
    NoteReviewRequest,
    QAPair,
    StudentNotesGenerateResponse,
)
from app.services.gemini_key_manager import (
    generate_with_fallback,
    generate_with_uploaded_file_fallback,
)
from app.services import vertex_ai

router = APIRouter()
logger = logging.getLogger("tamgam.notes")

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
ALLOWED_TEXT_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}
LATEX_PROMPT = r"""
You are given a class lecture PDF.

Return ONLY a complete, compilable LaTeX document (no markdown, no backticks).
It must compile with XeLaTeX.

Formatting requirements:
- Use \documentclass{article}
- Use headings: \section{}, \subsection{}, \subsubsection{}
- Bold important keywords using \textbf{}
- ALL formulas must be proper math (not plain text):
  - Use display math: \[ ... \] or equation environment.
  - Use \frac{}{}, superscripts, parentheses, etc.
  - Example formats:
    \[
      y\% \text{ of } 80 = \frac{y}{100}\times 80
    \]
    \[
      A = p(1 + rt)
    \]
    \[
      A = p(1 + r)^t
    \]
- Add a final \section{Quick Revision Summary} with 8–15 bullets.
- Add a final \section{Possible Exam Questions} with 8–12 questions.

Output must start with \documentclass and end with \end{document}.
"""


def _file_ext(name: str) -> str:
    lower = (name or "").lower()
    idx = lower.rfind(".")
    return lower[idx:] if idx >= 0 else ""


async def _read_optional_file(file: Optional[UploadFile]) -> tuple[Optional[str], Optional[str], Optional[int], Optional[bytes]]:
    if file is None:
        return None, None, None, None
    file_name = file.filename or "upload"
    ext = _file_ext(file_name)
    if ext not in ALLOWED_TEXT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported file type. Allowed: .txt, .md, .docx, .pdf")
    file_bytes = await file.read()
    file_size = len(file_bytes or b"")
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if file_size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Max size is 4 MB.")
    return file_name, file.content_type or "", file_size, file_bytes


def _extract_text(file_name: str, file_bytes: bytes) -> str:
    lower = (file_name or "").lower()
    if lower.endswith(".txt") or lower.endswith(".md"):
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("latin-1", errors="ignore")
    if lower.endswith(".docx"):
        try:
            from docx import Document

            doc = Document(BytesIO(file_bytes))
            return "\n".join(p.text.strip() for p in doc.paragraphs if p.text and p.text.strip())
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse DOCX file: {exc}") from exc
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(file_bytes))
            pages = []
            for page in reader.pages:
                pages.append((page.extract_text() or "").strip())
            return "\n".join(p for p in pages if p)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse PDF file: {exc}") from exc
    raise HTTPException(status_code=415, detail="Unsupported file type.")


def _contains_single_chapter(text: str) -> bool:
    sample = (text or "")[:20000]
    if not sample.strip():
        return False
    heading_pattern = re.compile(r"(?im)^\s*(chapter|lesson)\s*([0-9ivxlcdm]+)?\s*[:.\-]?\s*(.+)?$")
    headings = []
    for match in heading_pattern.finditer(sample):
        num = (match.group(2) or "").strip().lower()
        title = (match.group(3) or "").strip().lower()
        headings.append(f"{num}|{title}")
    headings = [h for h in headings if h.strip("|")]
    if len(set(headings)) > 1:
        return False

    chapter_nums = set(re.findall(r"(?i)\b(?:chapter|lesson)\s+([0-9]{1,2})\b", sample))
    if len(chapter_nums) > 1:
        return False
    return True


def _student_profile_for_user(user_id: UUID, db: Session) -> Optional[StudentProfile]:
    return db.query(StudentProfile).filter(StudentProfile.user_id == user_id).first()


def _understanding_level_for_subject(student_id: UUID, subject: str, db: Session) -> int:
    profiles = db.query(StudentUnderstandingProfile).filter(
        StudentUnderstandingProfile.student_id == student_id
    ).all()
    if not profiles:
        return 3
    for profile in profiles:
        if (profile.subject or "").strip().lower() == (subject or "").strip().lower():
            return int(profile.current_level or 3)
    for profile in profiles:
        if (profile.subject or "").strip().lower() == "general":
            return int(profile.current_level or 3)
    return int(profiles[0].current_level or 3)


def _fallback_personalized_notes(subject: str, standard: int, chapter: str) -> str:
    return (
        f"# {subject} - Class {standard} - {chapter}\n\n"
        "## Exam-Focused Summary\n"
        "- Explain the chapter in 8-10 crisp points with definitions and laws.\n"
        "- Add the most frequently asked board-exam statements.\n\n"
        "## Formula Sheet (with SI units)\n"
        "- List all important formulas used in this chapter.\n"
        "- Mention symbol meanings, units, and dimensional hint where useful.\n\n"
        "## Important Derivations\n"
        "- Write stepwise derivation outlines likely asked in exams.\n"
        "- Mention where students lose marks.\n\n"
        "## Solved Numericals\n"
        "1. Easy level with all steps and unit conversion.\n"
        "2. Medium level with exam-style wording and final boxed answer.\n"
        "3. One higher-order/application question.\n\n"
        "## Most Common Mistakes\n"
        "- Wrong units/conversions.\n"
        "- Sign convention errors.\n"
        "- Missing formula conditions/assumptions.\n\n"
        "## Rapid Revision (1-Day Before Exam)\n"
        "- 10 one-line oral questions.\n"
        "- 5 short-answer questions.\n"
        "- 3 long-answer probable questions.\n"
    )


def _extract_notes_markdown(raw_text: str) -> str:
    """
    Robustly extract notes markdown from model output.
    Accepts:
    - pure markdown
    - strict JSON object with `notes_markdown`
    - JSON wrapped in prose/code-fences
    """
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return ""

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    # 1) Direct JSON parse
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            note = str(parsed.get("notes_markdown") or "").strip()
            if note:
                return note
    except Exception:
        pass

    # 2) Extract first JSON object from mixed text
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        maybe_json = cleaned[start : end + 1]
        try:
            parsed = json.loads(maybe_json)
            if isinstance(parsed, dict):
                note = str(parsed.get("notes_markdown") or "").strip()
                if note:
                    return note
        except Exception:
            pass

    # 3) If model returned markdown directly
    if len(cleaned) > 120:
        return cleaned
    return ""


def _extract_latex_document(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return ""
    start = text.find(r"\documentclass")
    end = text.rfind(r"\end{document}")
    if start == -1 or end == -1 or end <= start:
        return ""
    end += len(r"\end{document}")
    return text[start:end].strip()


def _compile_latex_with_xelatex(latex_text: str) -> Optional[bytes]:
    if not latex_text.strip():
        return None
    if shutil.which("xelatex") is None:
        logger.warning("xelatex not found; skipping LaTeX compilation check")
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="notes_latex_") as tmpdir:
            tex_path = f"{tmpdir}/Lecture_Notes.tex"
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(latex_text)
            subprocess.run(["xelatex", "-interaction=nonstopmode", tex_path], check=False, cwd=tmpdir)
            subprocess.run(["xelatex", "-interaction=nonstopmode", tex_path], check=False, cwd=tmpdir)
            pdf_path = f"{tmpdir}/Lecture_Notes.pdf"
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    return f.read()
            return None
    except Exception:
        return None


def _latex_to_markdown(latex_text: str, subject: str, standard: int, chapter: str) -> str:
    text = (latex_text or "").replace("\r\n", "\n")
    if not text.strip():
        return ""

    text = re.sub(r"(?s)\\documentclass.*?\\begin\{document\}", "", text).strip()
    text = re.sub(r"\\end\{document\}\s*$", "", text).strip()
    text = re.sub(r"\\title\{(.+?)\}", r"# \1", text)
    text = re.sub(r"\\section\{(.+?)\}", r"\n## \1\n", text)
    text = re.sub(r"\\subsection\{(.+?)\}", r"\n### \1\n", text)
    text = re.sub(r"\\subsubsection\{(.+?)\}", r"\n#### \1\n", text)
    text = re.sub(r"\\textbf\{(.+?)\}", r"**\1**", text)
    text = re.sub(r"\\begin\{itemize\}", "", text)
    text = re.sub(r"\\end\{itemize\}", "", text)
    text = re.sub(r"\\begin\{enumerate\}", "", text)
    text = re.sub(r"\\end\{enumerate\}", "", text)
    text = re.sub(r"(?m)^\s*\\item\s+", "- ", text)
    text = re.sub(r"\\\[(.*?)\\\]", r"\n$$\1$$\n", text, flags=re.S)
    text = re.sub(r"\\begin\{equation\}(.*?)\\end\{equation\}", r"\n$$\1$$\n", text, flags=re.S)
    text = re.sub(r"\\%", "%", text)
    text = re.sub(r"\\times", "x", text)
    text = re.sub(r"\\text\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if not text.startswith("#"):
        text = f"# {subject} - Class {standard} - {chapter}\n\n{text}"
    return text.strip()


def _generate_personalized_notes(
    *,
    subject: str,
    standard: int,
    chapter: str,
    understanding_level: int,
    weak_sections: list[str],
    chapter_text: Optional[str],
    exam_questions_text: Optional[str],
) -> str:
    chapter_context = (chapter_text or "").strip()
    exam_context = (exam_questions_text or "").strip()
    prompt = f"""
You are Diya, an expert tutor for Indian school students (CBSE/ICSE/state boards).
Create HIGH-QUALITY, EXAM-ORIENTED notes for exactly one chapter.

Student metadata:
- Subject: {subject}
- Standard: {standard}
- Chapter/Lesson: {chapter}
- Understanding level (1-5): {understanding_level}
- Weak sections: {", ".join(weak_sections) if weak_sections else "none provided"}

Rules:
- Keep explanation depth aligned to the understanding level.
- Focus extra on weak sections.
- If past exam questions are provided, include exam patterns and answer approaches.
- Cover only this one chapter/lesson, not multiple chapters.
- Make it directly useful for exams: include definitions, formulas with units, derivations, solved numericals, common mistakes, likely questions, and a final quick-revision checklist.
- Avoid generic placeholders.
- Return ONLY JSON with this exact shape:
{{
  "notes_markdown": "detailed markdown notes"
}}

Uploaded chapter content (optional):
{chapter_context[:14000] if chapter_context else "Not provided. Use textbook knowledge for this chapter."}

Uploaded past exam questions (optional):
{exam_context[:7000] if exam_context else "Not provided."}
"""
    try:
        raw = generate_with_fallback(prompt, model_name="gemini-2.0-flash")
        notes = _extract_notes_markdown(raw or "")

        if notes and "Definition of key terms" not in notes:
            candidate = _clean_notes_markdown(notes)
            if _notes_quality_ok(candidate, chapter):
                return candidate

            # Rewrite pass if first output is generic/off-topic.
            rewrite_prompt = f"""
Rewrite the following notes into high-quality, exam-oriented notes for:
- Subject: {subject}
- Standard: {standard}
- Chapter: {chapter}

Strict requirements:
- Chapter-specific only (do not switch chapter).
- Include: definitions, formula sheet with units, derivations, solved numericals, common mistakes, probable exam questions, quick revision list.
- Keep markdown structure with H2/H3 headings and bullets.
- Do not include placeholders.

Draft notes:
{candidate[:20000]}

Return ONLY JSON:
{{
  "notes_markdown": "rewritten markdown notes"
}}
"""
            rewrite_raw = generate_with_fallback(rewrite_prompt, model_name="gemini-2.0-flash")
            rewritten = _extract_notes_markdown(rewrite_raw or "")
            rewritten = _clean_notes_markdown(rewritten)
            if _notes_quality_ok(rewritten, chapter):
                return rewritten
    except Exception:
        pass
    return ""


def _generate_personalized_notes_from_pdf(
    *,
    subject: str,
    standard: int,
    chapter: str,
    understanding_level: int,
    weak_sections: list[str],
    chapter_pdf_bytes: bytes,
    chapter_pdf_name: str,
    exam_questions_text: Optional[str],
) -> tuple[str, Optional[str]]:
    _ = understanding_level, weak_sections, exam_questions_text  # Metadata retained for endpoint compatibility.
    try:
        raw = generate_with_uploaded_file_fallback(
            prompt=LATEX_PROMPT,
            file_bytes=chapter_pdf_bytes,
            file_name=chapter_pdf_name,
            model_name="gemini-2.0-flash",
        )
        latex_text = _extract_latex_document(raw or "")
        if not latex_text:
            return "", None

        pdf_bytes = _compile_latex_with_xelatex(latex_text)
        md = _latex_to_markdown(latex_text, subject, standard, chapter)
        candidate = _clean_notes_markdown(md)
        if candidate and len(candidate) >= 350:
            pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii") if pdf_bytes else None
            return candidate, pdf_b64
    except Exception:
        pass
    return "", None


def _clean_notes_markdown(notes: str) -> str:
    """
    Normalize common LLM/PDF-unfriendly characters and accidental spaced letters.
    """
    text = (notes or "").replace("\r\n", "\n").strip()
    if not text:
        return text

    # Replace unicode symbols that often break in downstream PDF/copy flows.
    text = (
        text.replace("θ", "theta")
        .replace("¸", "theta")
        .replace("×", "x")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
    )

    # Collapse patterns like "F o r m u l a" -> "Formula"
    def _collapse_spaced_letters(match):
        token = match.group(0)
        return token.replace(" ", "")

    text = re.sub(r"\b(?:[A-Za-z]\s){3,}[A-Za-z]\b", _collapse_spaced_letters, text)

    # Line-level repair for heavily spaced alphabetic text.
    repaired_lines = []
    for line in text.split("\n"):
        tokens = line.split()
        alpha_short = 0
        alpha_total = 0
        for t in tokens:
            cleaned = re.sub(r"[^A-Za-z]", "", t)
            if cleaned:
                alpha_total += 1
                if len(cleaned) <= 2:
                    alpha_short += 1
        suspicious = alpha_total >= 6 and (alpha_short / max(alpha_total, 1)) >= 0.5
        if suspicious:
            line = re.sub(r"(?<=[A-Za-z])\s(?=[A-Za-z])", "", line)
        repaired_lines.append(line)
    text = "\n".join(repaired_lines)

    # Remove repeated excessive spaces
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _notes_quality_ok(notes: str, chapter: str) -> bool:
    content = (notes or "").strip()
    if len(content) < 600:
        return False
    bad_markers = [
        "definition of key terms",
        "main formulae/rules to remember",
        "start with a direct textbook-style problem",
        "typical mistakes and how to avoid them",
    ]
    low = content.lower()
    if any(m in low for m in bad_markers):
        return False
    chapter_key = re.sub(r"[^a-z0-9 ]", "", (chapter or "").lower()).strip()
    if chapter_key:
        # Require chapter keyword presence near the top section.
        if chapter_key not in re.sub(r"[^a-z0-9 ]", "", low[:2500]):
            return False
    return True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_subscribed(user_id, db):
    return db.query(Subscription).filter(
        and_(Subscription.user_id == user_id, Subscription.status == "active")
    ).first() is not None


def _parse_content(raw: dict) -> NoteContent:
    """Parse raw dict from DB/Gemini into NoteContent schema."""
    return NoteContent(
        summary=raw.get("summary", ""),
        key_points=raw.get("key_points", []),
        detailed_notes=raw.get("detailed_notes", ""),
        qa_pairs=[QAPair(**qa) for qa in raw.get("qa_pairs", [])],
    )


def _build_response(note: Note, viewer: User, db) -> NoteResponse:
    show_content = False
    content_gated = False
    if viewer:
        if viewer.role in ("teacher", "admin"):
            show_content = True
        elif viewer.role == "student":
            if _is_subscribed(viewer.id, db):
                show_content = True
            elif note.content:
                content_gated = True

    content = None
    if show_content and note.content:
        content = _parse_content(note.content)

    return NoteResponse(
        id=note.id,
        class_id=note.class_id,
        status=note.status,
        content=content,
        content_gated=content_gated,
        teacher_reviewed=note.teacher_reviewed,
        teacher_review_notes=note.teacher_review_notes,
        ai_model_used=note.ai_model_used,
        generation_prompt_tokens=note.generation_prompt_tokens,
        generation_output_tokens=note.generation_output_tokens,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _run_generation(note_id: UUID, transcript_text: str, db: Session):
    """Background task: call Gemini and store structured notes."""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        return

    note.status = "generating"
    db.commit()

    try:
        result = vertex_ai.generate_notes(transcript_text)
        if result:
            note.content = result
            note.status = "completed"
            note.ai_model_used = "gemini-2.5-flash"
            # Update class notes_status
            cls = db.query(Class).filter(Class.id == note.class_id).first()
            if cls:
                cls.notes_status = "completed"
        else:
            note.status = "failed"
            cls = db.query(Class).filter(Class.id == note.class_id).first()
            if cls:
                cls.notes_status = "failed"
    except Exception as e:
        note.status = "failed"
        logger.exception("Notes generation failed: %s", e)

    note.updated_at = datetime.now(timezone.utc)
    db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/student/generate",
    response_model=StudentNotesGenerateResponse,
    summary="Generate personalized one-chapter notes for student",
)
async def generate_student_notes(
    subject: str = Form(...),
    standard: int = Form(...),
    chapter: str = Form(...),
    chapter_file: UploadFile = File(...),
    exam_questions_file: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")

    subject_clean = (subject or "").strip()
    chapter_clean = (chapter or "").strip()
    if not subject_clean:
        raise HTTPException(status_code=422, detail="subject is required.")
    if not chapter_clean:
        raise HTTPException(status_code=422, detail="chapter/lesson is required.")
    if standard < 1 or standard > 12:
        raise HTTPException(status_code=422, detail="standard must be between 1 and 12.")

    student = _student_profile_for_user(current_user.id, db)
    if not student:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    now = datetime.now(timezone.utc)
    lock_cutoff = now - timedelta(days=7)
    lock_row = db.query(StudentNoteRequest).filter(
        and_(
            StudentNoteRequest.student_id == student.id,
            func.lower(StudentNoteRequest.subject) == subject_clean.lower(),
            func.lower(StudentNoteRequest.chapter) == chapter_clean.lower(),
            StudentNoteRequest.created_at >= lock_cutoff,
        )
    ).order_by(StudentNoteRequest.created_at.desc()).first()
    if lock_row:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Notes for {subject_clean} - {chapter_clean} were already generated recently. "
                f"Try again after {lock_row.next_allowed_at.isoformat()}."
            ),
        )

    chapter_name, _chapter_mime, _chapter_size, chapter_bytes = await _read_optional_file(chapter_file)
    if chapter_bytes is None:
        raise HTTPException(status_code=422, detail="chapter_file is required.")
    exam_name, exam_mime, exam_size, exam_bytes = await _read_optional_file(exam_questions_file)

    chapter_ext = _file_ext(chapter_name or "")
    chapter_text = None
    if chapter_bytes:
        if chapter_ext != ".pdf":
            chapter_text = _extract_text(chapter_name or "", chapter_bytes)
            if not chapter_text.strip():
                raise HTTPException(status_code=400, detail="Could not extract text from uploaded chapter file.")
            if not _contains_single_chapter(chapter_text):
                raise HTTPException(
                    status_code=422,
                    detail="Uploaded chapter file appears to contain multiple chapters/lessons. Upload only one chapter.",
                )
        else:
            # Prefer direct PDF-to-Gemini upload for better formatting and math fidelity.
            try:
                chapter_text = _extract_text(chapter_name or "", chapter_bytes)
                if chapter_text.strip() and not _contains_single_chapter(chapter_text):
                    raise HTTPException(
                        status_code=422,
                        detail="Uploaded chapter file appears to contain multiple chapters/lessons. Upload only one chapter.",
                    )
            except HTTPException:
                raise
            except Exception:
                chapter_text = None

    exam_text = None
    if exam_bytes:
        exam_text = _extract_text(exam_name or "", exam_bytes)
        if not exam_text.strip():
            raise HTTPException(status_code=400, detail="Could not extract text from uploaded exam questions file.")

    understanding_level = _understanding_level_for_subject(student.id, subject_clean, db)
    weak_sections = [w.strip() for w in (student.improvement_areas or []) if isinstance(w, str) and w.strip()]

    request_row = StudentNoteRequest(
        student_id=student.id,
        standard=standard,
        subject=subject_clean,
        chapter=chapter_clean,
        chapter_uploaded=1 if chapter_bytes else 0,
        understanding_level=understanding_level,
        weak_sections=weak_sections or None,
        exam_file_name=exam_name,
        exam_file_mime=exam_mime,
        exam_file_size_bytes=exam_size,
        exam_file_bytes=exam_bytes,
        generation_status="completed",
        created_at=now,
        next_allowed_at=now + timedelta(days=7),
    )
    db.add(request_row)
    db.flush()

    notes_pdf_base64 = None
    if chapter_ext == ".pdf":
        notes_markdown, notes_pdf_base64 = _generate_personalized_notes_from_pdf(
            subject=subject_clean,
            standard=standard,
            chapter=chapter_clean,
            understanding_level=understanding_level,
            weak_sections=weak_sections,
            chapter_pdf_bytes=chapter_bytes,
            chapter_pdf_name=chapter_name or "chapter.pdf",
            exam_questions_text=exam_text,
        )
    else:
        notes_markdown = _generate_personalized_notes(
            subject=subject_clean,
            standard=standard,
            chapter=chapter_clean,
            understanding_level=understanding_level,
            weak_sections=weak_sections,
            chapter_text=chapter_text,
            exam_questions_text=exam_text,
        )
    notes_markdown = _clean_notes_markdown(notes_markdown)
    if not notes_markdown or len(notes_markdown.strip()) < 350:
        logger.warning(
            "Student notes fallback used: empty/short output (subject=%s, standard=%s, chapter=%s)",
            subject_clean,
            standard,
            chapter_clean,
        )
        notes_markdown = _fallback_personalized_notes(subject_clean, standard, chapter_clean)

    db.commit()
    db.refresh(request_row)

    return StudentNotesGenerateResponse(
        request_id=request_row.id,
        subject=subject_clean,
        standard=standard,
        chapter=chapter_clean,
        understanding_level=understanding_level,
        weak_sections=weak_sections,
        notes_markdown=notes_markdown,
        notes_pdf_base64=notes_pdf_base64,
        used_uploaded_chapter=chapter_bytes is not None,
        used_uploaded_exam_questions=exam_bytes is not None,
        created_at=request_row.created_at,
        next_allowed_at=request_row.next_allowed_at,
    )


@router.post(
    "/{class_id}/generate",
    response_model=NoteResponse,
    status_code=201,
    summary="Generate AI notes from transcript (teacher only)",
)
def generate_notes(
    class_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Trigger AI notes generation for a class.
    Requires a completed transcript.
    Runs Gemini in background -- poll GET to check status.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    transcript = db.query(Transcript).filter(
        and_(Transcript.class_id == class_id, Transcript.status == "completed")
    ).first()
    if not transcript:
        raise HTTPException(
            status_code=422,
            detail="No completed transcript found. Upload and process a transcript first.",
        )

    # Check no note already generating or completed
    existing = db.query(Note).filter(Note.class_id == class_id).first()
    if existing:
        if existing.status in ("generating", "completed"):
            raise HTTPException(
                status_code=409,
                detail=f"Notes already exist with status '{existing.status}'. Use PATCH to edit or review.",
            )
        # Failed note -- allow regeneration
        db.delete(existing)
        db.flush()

    note = Note(
        class_id=class_id,
        status="pending",
        teacher_reviewed=False,
    )
    db.add(note)
    cls.notes_status = "pending"
    db.commit()
    db.refresh(note)

    background_tasks.add_task(_run_generation, note.id, transcript.raw_text, db)

    return _build_response(note, current_user, db)


@router.get(
    "/{class_id}",
    response_model=NoteResponse,
    summary="Get notes for a class",
)
def get_notes(
    class_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Get AI-generated notes for a class.
    Content is subscription-gated for students.
    Teachers always see their own class notes.
    """
    note = db.query(Note).filter(Note.class_id == class_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="No notes found for this class.")
    return _build_response(note, current_user, db)


@router.patch(
    "/{class_id}/review",
    response_model=NoteResponse,
    summary="Teacher reviews AI notes (approve or reject)",
)
def review_notes(
    class_id: UUID,
    payload: NoteReviewRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher approves or rejects AI-generated notes.
    Rejected notes can be regenerated or manually edited.
    Students only see notes that have been approved.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    note = db.query(Note).filter(Note.class_id == class_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="No notes found for this class.")
    if note.status != "completed":
        raise HTTPException(
            status_code=422,
            detail=f"Notes are not ready for review (status: {note.status}).",
        )

    if payload.approved:
        note.status = "completed"
        note.teacher_reviewed = True
        note.teacher_review_notes = payload.review_notes
    else:
        if not payload.review_notes:
            raise HTTPException(
                status_code=422,
                detail="review_notes is required when rejecting notes.",
            )
        note.status = "rejected"
        note.teacher_reviewed = True
        note.teacher_review_notes = payload.review_notes
        cls.notes_status = "rejected"

    note.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(note)
    return _build_response(note, current_user, db)


@router.patch(
    "/{class_id}",
    response_model=NoteResponse,
    summary="Teacher edits note content directly",
)
def edit_notes(
    class_id: UUID,
    payload: NoteEditRequest,
    current_user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher manually edits note content.
    Auto-marks as reviewed and approved.
    """
    tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    cls = db.query(Class).filter(
        and_(Class.id == class_id, Class.teacher_id == tp.id)
    ).first()
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found.")

    note = db.query(Note).filter(Note.class_id == class_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="No notes found for this class.")

    note.content = payload.content.model_dump()
    note.status = "completed"
    note.teacher_reviewed = True
    note.updated_at = datetime.now(timezone.utc)
    cls.notes_status = "completed"

    db.commit()
    db.refresh(note)
    return _build_response(note, current_user, db)
