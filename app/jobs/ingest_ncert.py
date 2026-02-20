# app/jobs/ingest_ncert.py
# One-time (and re-runnable) ingestion job for NCERT Mathematics textbooks.
#
# Downloads Class 8, 9, 10 NCERT Mathematics PDFs from the official NCERT
# Digital Library, extracts text, chunks it semantically, and stores
# embeddings in content_embeddings with ncert_book content_type.
#
# Usage:
#   python -m app.jobs.ingest_ncert                      # all grades
#   python -m app.jobs.ingest_ncert --grades 9 10        # specific grades
#   python -m app.jobs.ingest_ncert --force              # re-embed existing
#   python -m app.jobs.ingest_ncert --dry-run            # count chunks only
#
# NCERT PDFs are downloaded once and cached in /tmp/ncert_pdfs/.
# Set NCERT_PDF_DIR env var to use a persistent local path instead.
#
# Idempotent: skips grades already embedded unless --force.

from __future__ import annotations

import argparse
import io
import logging
import os
import re
import sys
import time
import app.db.base
from app.models.ai import ContentEmbedding
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── NCERT PDF catalogue ───────────────────────────────────────────────────────
# Official NCERT Digital Textbooks (epub/pdf endpoint).
# Format: https://ncert.nic.in/textbook/pdf/{code}.zip  (chapter-level zips)
#
# We use the single-file chapter PDFs which are publicly accessible.
# Each book is a list of (chapter_num, chapter_title, pdf_url) tuples.
#
# URLs follow the pattern:
#   https://ncert.nic.in/textbook/pdf/{subject_code}{grade_code}{chapter:02d}.pdf
#
# Subject codes: hemh = Class 8 Math (Hindi+English Medium unified)
#                iemh = Class 9 Math
#                jemh = Class 10 Math

NCERT_CATALOGUE: dict[int, dict] = {
    8: {
        "subject": "Mathematics",
        "board": "NCERT",
        "code_prefix": "hemh1",   # hemh1{chapter:02d}.pdf
        "chapters": [
            (1,  "Rational Numbers"),
            (2,  "Linear Equations in One Variable"),
            (3,  "Understanding Quadrilaterals"),
            (4,  "Data Handling"),
            (5,  "Squares and Square Roots"),
            (6,  "Cubes and Cube Roots"),
            (7,  "Comparing Quantities"),
            (8,  "Algebraic Expressions and Identities"),
            (9,  "Mensuration"),
            (10, "Exponents and Powers"),
            (11, "Direct and Inverse Proportions"),
            (12, "Factorisation"),
            (13, "Introduction to Graphs"),
        ],
    },
    9: {
        "subject": "Mathematics",
        "board": "NCERT",
        "code_prefix": "iemh1",
        "chapters": [
            (1,  "Number Systems"),
            (2,  "Polynomials"),
            (3,  "Coordinate Geometry"),
            (4,  "Linear Equations in Two Variables"),
            (5,  "Introduction to Euclid's Geometry"),
            (6,  "Lines and Angles"),
            (7,  "Triangles"),
            (8,  "Quadrilaterals"),
            (9,  "Circles"),
            (10, "Heron's Formula"),
            (11, "Surface Areas and Volumes"),
            (12, "Statistics"),
        ],
    },
    10: {
        "subject": "Mathematics",
        "board": "NCERT",
        "code_prefix": "jemh1",
        "chapters": [
            (1,  "Real Numbers"),
            (2,  "Polynomials"),
            (3,  "Pair of Linear Equations in Two Variables"),
            (4,  "Quadratic Equations"),
            (5,  "Arithmetic Progressions"),
            (6,  "Triangles"),
            (7,  "Coordinate Geometry"),
            (8,  "Introduction to Trigonometry"),
            (9,  "Some Applications of Trigonometry"),
            (10, "Circles"),
            (11, "Areas Related to Circles"),
            (12, "Surface Areas and Volumes"),
            (13, "Statistics"),
            (14, "Probability"),
        ],
    },
}

NCERT_PDF_BASE = "https://ncert.nic.in/textbook/pdf"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class NcertChunk:
    grade: int
    chapter_num: int
    chapter_title: str
    subject: str
    chunk_index: int
    text: str
    token_count: int = field(init=False)

    def __post_init__(self):
        self.token_count = len(self.text.split())


# ── PDF download & text extraction ───────────────────────────────────────────

def _pdf_cache_dir() -> Path:
    """Return local cache directory for downloaded PDFs."""
    custom = os.environ.get("NCERT_PDF_DIR")
    if custom:
        path = Path(custom)
    else:
        path = Path("/tmp/ncert_pdfs")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download_pdf(url: str, cache_path: Path, retries: int = 3) -> Optional[bytes]:
    """
    Download a PDF from url, cache locally, and return raw bytes.
    Returns None on failure after retries.
    """
    if cache_path.exists():
        log.debug("Cache hit: %s", cache_path.name)
        return cache_path.read_bytes()

    import urllib.request
    import urllib.error

    for attempt in range(1, retries + 1):
        try:
            log.info("Downloading (attempt %d/%d): %s", attempt, retries, url)
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            cache_path.write_bytes(data)
            log.info("Saved %d KB → %s", len(data) // 1024, cache_path.name)
            return data
        except urllib.error.HTTPError as e:
            log.warning("HTTP %s for %s", e.code, url)
            return None   # 404 means chapter doesn't exist — skip
        except Exception as exc:
            log.warning("Download failed: %s", exc)
            if attempt < retries:
                time.sleep(2 ** attempt)

    return None


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract plain text from a PDF using PyMuPDF (fitz).
    Joins all pages, normalises whitespace.
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text("text"))
        doc.close()
        raw = "\n".join(pages)
        # Collapse excessive blank lines but preserve paragraph breaks
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()
    except Exception as exc:
        log.error("PDF text extraction failed: %s", exc)
        return ""


# ── Text cleaning ─────────────────────────────────────────────────────────────

# Patterns to strip from NCERT PDFs before chunking
_NOISE_PATTERNS = [
    r"^\s*\d+\s*$",                     # Standalone page numbers
    r"^MATHEMATICS\s*$",                # Running header
    r"^Class\s+(VIII|IX|X|8|9|10)\s*$",# Running grade header
    r"^NCERT\s*$",
    r"^\s*©\s*NCERT.*$",               # Copyright lines
    r"^not to be republished.*$",       # Watermark text
    r"^FREE DISTRIBUTION.*$",
    r"^\s*EXERCISE\s+\d+[\.\d]*\s*$",  # Exercise headings (kept in context but cleaned)
]
_NOISE_RE = re.compile(
    "|".join(f"({p})" for p in _NOISE_PATTERNS),
    re.IGNORECASE | re.MULTILINE,
)


def _clean_text(text: str) -> str:
    """Remove NCERT PDF noise (headers, page numbers, watermarks)."""
    lines = text.split("\n")
    clean = []
    for line in lines:
        if _NOISE_RE.match(line.strip()):
            continue
        clean.append(line)
    result = "\n".join(clean)
    # Collapse runs of blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_chapter_text(
    text: str,
    chapter_num: int,
    chapter_title: str,
    grade: int,
    subject: str,
    chunk_size: int = 400,
    overlap: int = 40,
) -> List[NcertChunk]:
    """
    Split a chapter's text into overlapping word-based chunks.
    Each chunk is prefixed with grade/chapter context so the embedding
    captures the educational context even without metadata filtering.

    e.g. "Class 9 Mathematics – Polynomials: <chunk text>"
    """
    prefix = f"Class {grade} {subject} – {chapter_title}: "

    words = text.split()
    if not words:
        return []

    chunks = []
    start = 0
    idx = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_words = words[start:end]
        chunk_text = prefix + " ".join(chunk_words)

        chunks.append(
            NcertChunk(
                grade=grade,
                chapter_num=chapter_num,
                chapter_title=chapter_title,
                subject=subject,
                chunk_index=idx,
                text=chunk_text,
            )
        )
        idx += 1
        if end == len(words):
            break
        start += chunk_size - overlap

    return chunks


# ── Per-grade ingestion ───────────────────────────────────────────────────────

def _already_ingested(grade: int, db) -> bool:
    """Check if any ncert_book chunks exist for this grade."""
    from sqlalchemy import text as sqla_text
    result = db.execute(
        sqla_text(
            "SELECT COUNT(*) FROM content_embeddings "
            "WHERE content_type = 'ncert_book' AND ncert_grade = :grade"
        ),
        {"grade": grade},
    ).scalar()
    return (result or 0) > 0


def _delete_grade(grade: int, db) -> int:
    """Delete all ncert_book chunks for a grade. Returns deleted count."""
    from sqlalchemy import text as sqla_text
    result = db.execute(
        sqla_text(
            "DELETE FROM content_embeddings "
            "WHERE content_type = 'ncert_book' AND ncert_grade = :grade "
            "RETURNING id"
        ),
        {"grade": grade},
    )
    count = result.rowcount
    db.flush()
    log.info("Deleted %d existing chunks for grade %d", count, grade)
    return count


def _get_embedding(text: str) -> Optional[list]:
    """Generate embedding. Returns None in dev mode."""
    try:
        from app.services.vertex_ai import generate_embedding
        return generate_embedding(text)
    except Exception:
        return None


def ingest_grade(
    grade: int,
    db,
    *,
    force: bool = False,
    dry_run: bool = False,
    chunk_size: int = 400,
    overlap: int = 40,
) -> dict:
    """
    Ingest all chapters for a single grade into content_embeddings.

    Args:
        grade:      8, 9, or 10
        db:         SQLAlchemy session
        force:      Delete existing embeddings and re-ingest
        dry_run:    Parse and count chunks without writing to DB
        chunk_size: Words per chunk
        overlap:    Overlap words between chunks

    Returns:
        dict with keys: grade, chapters_processed, total_chunks, skipped, status
    """
    from app.models.ai import ContentEmbedding

    if grade not in NCERT_CATALOGUE:
        raise ValueError(f"Grade {grade} not in catalogue. Valid: {list(NCERT_CATALOGUE)}")

    book = NCERT_CATALOGUE[grade]
    subject = book["subject"]
    code_prefix = book["code_prefix"]
    chapters = book["chapters"]

    if not dry_run:
        if _already_ingested(grade, db):
            if not force:
                log.info(
                    "Grade %d already ingested. Use --force to re-embed.", grade
                )
                return {
                    "grade": grade,
                    "chapters_processed": 0,
                    "total_chunks": 0,
                    "skipped": True,
                    "status": "already_ingested",
                }
            _delete_grade(grade, db)

    cache_dir = _pdf_cache_dir()
    chapters_processed = 0
    total_chunks = 0

    for chapter_num, chapter_title in chapters:
        pdf_filename = f"{code_prefix}{chapter_num:02d}.pdf"
        pdf_url = f"{NCERT_PDF_BASE}/{pdf_filename}"
        cache_path = cache_dir / pdf_filename

        # Download
        pdf_bytes = _download_pdf(pdf_url, cache_path)
        if pdf_bytes is None:
            log.warning(
                "Skipping Grade %d Chapter %d (%s) — download failed",
                grade, chapter_num, chapter_title,
            )
            continue

        # Extract + clean text
        raw_text = _extract_text_from_pdf(pdf_bytes)
        if not raw_text:
            log.warning(
                "Skipping Grade %d Chapter %d — no text extracted", grade, chapter_num
            )
            continue

        clean = _clean_text(raw_text)
        word_count = len(clean.split())
        log.info(
            "Grade %d | Ch %2d %-45s | %d words",
            grade, chapter_num, chapter_title, word_count,
        )

        # Chunk
        chunks = _chunk_chapter_text(
            clean,
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            grade=grade,
            subject=subject,
            chunk_size=chunk_size,
            overlap=overlap,
        )

        if dry_run:
            log.info(
                "  [dry-run] would produce %d chunks", len(chunks)
            )
            total_chunks += len(chunks)
            chapters_processed += 1
            continue

        # Embed + insert in batches of 20 to avoid memory spikes
        batch_size = 20
        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start: batch_start + batch_size]
            for chunk in batch:
                embedding = _get_embedding(chunk.text)
                time.sleep(15) # Rate limit to avoid overwhelming Vertex AI in case of large batches
                row = ContentEmbedding(
                    # No transcript/note/post FK — NCERT is a standalone source
                    transcript_id=None,
                    note_id=None,
                    post_id=None,
                    class_id=None,          # Not tied to any specific TamGam class
                    subject=subject,
                    content_type="ncert_book",
                    chunk_text=chunk.text,
                    chunk_index=chunk.chunk_index,
                    token_count=chunk.token_count,
                    embedding=embedding,
                    # NCERT-specific metadata
                    ncert_grade=chunk.grade,
                    ncert_chapter=chunk.chapter_title,
                    ncert_chapter_num=chunk.chapter_num,
                )
                db.add(row)

            db.flush()   # Flush each batch to avoid huge pending transactions
            log.debug("Flushed batch %d–%d", batch_start, batch_start + len(batch))

        total_chunks += len(chunks)
        chapters_processed += 1

    if not dry_run:
        db.commit()
        log.info(
            "✓ Grade %d ingested: %d chapters, %d chunks",
            grade, chapters_processed, total_chunks,
        )

    return {
        "grade": grade,
        "chapters_processed": chapters_processed,
        "total_chunks": total_chunks,
        "skipped": False,
        "status": "dry_run" if dry_run else "completed",
    }


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Ingest NCERT Mathematics books (Class 8, 9, 10) into Diya's vector store."
    )
    parser.add_argument(
        "--grades",
        nargs="+",
        type=int,
        default=[8, 9, 10],
        choices=[8, 9, 10],
        help="Which grades to ingest (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing embeddings for the grade and re-ingest",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Parse PDFs and count chunks without writing to the database",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=400,
        dest="chunk_size",
        help="Words per chunk (default: 400)",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=40,
        help="Overlap words between consecutive chunks (default: 40)",
    )
    args = parser.parse_args()

    # Bootstrap path for running as __main__
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from app.db.session import SessionLocal

    db = SessionLocal()
    summary = []

    try:
        for grade in sorted(set(args.grades)):
            result = ingest_grade(
                grade,
                db,
                force=args.force,
                dry_run=args.dry_run,
                chunk_size=args.chunk_size,
                overlap=args.overlap,
            )
            summary.append(result)

        # Build HNSW index after all inserts (unless dry-run)
        if not args.dry_run:
            from app.services.vector_service import build_hnsw_index
            log.info("Building HNSW index …")
            ok = build_hnsw_index(db)
            if ok:
                log.info("✓ HNSW index ready")
            else:
                log.warning("HNSW index build failed — search will still work via seqscan")

    finally:
        db.close()

    # Print summary table
    print("\n" + "─" * 60)
    print(f"{'Grade':<8} {'Chapters':<12} {'Chunks':<12} {'Status'}")
    print("─" * 60)
    grand_total = 0
    for r in summary:
        print(
            f"  {r['grade']:<6} {r['chapters_processed']:<12} "
            f"{r['total_chunks']:<12} {r['status']}"
        )
        grand_total += r["total_chunks"]
    print("─" * 60)
    print(f"{'TOTAL':<8} {'':12} {grand_total:<12}")
    print("─" * 60 + "\n")


if __name__ == "__main__":
    main()