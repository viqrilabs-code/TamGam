# app/schemas/embedding.py

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class EmbedRequest(BaseModel):
    """Trigger embedding for a specific class's notes and transcript."""
    class_id: UUID


class EmbedStatusResponse(BaseModel):
    class_id: UUID
    transcript_chunks: int      # Number of transcript chunks embedded
    note_chunks: int            # Number of note chunks embedded
    total_chunks: int
    status: str                 # completed | partial | failed | skipped
    message: str


class MessageResponse(BaseModel):
    message: str