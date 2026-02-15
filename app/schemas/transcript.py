# app/schemas/transcript.py
# Pydantic request/response models for transcript endpoints

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


# ── Transcript ────────────────────────────────────────────────────────────────

class TranscriptLinkRequest(BaseModel):
    """Teacher links a Google Drive .docx to a class."""
    drive_file_id: str      # Google Drive file ID (from the URL)
    drive_file_name: Optional[str] = None


class TranscriptUpdateRequest(BaseModel):
    """Teacher can manually correct the extracted raw text."""
    raw_text: str


class TranscriptResponse(BaseModel):
    id: UUID
    class_id: UUID
    drive_file_id: Optional[str] = None
    drive_file_name: Optional[str] = None
    status: str              # pending | processing | completed | failed
    raw_text: Optional[str] = None   # None if not yet processed or subscription gated
    word_count: Optional[int] = None
    raw_text_gated: bool     # True = text exists but hidden (no subscription)
    created_at: datetime
    updated_at: datetime


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str