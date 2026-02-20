# app/schemas/admin_books.py

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class BookUploadResponse(BaseModel):
    id: UUID
    title: str
    filename: str
    file_size: int
    embed_status: str
    message: str


class BookListResponse(BaseModel):
    id: UUID
    title: str
    author: Optional[str] = None
    subject: Optional[str] = None
    filename: str
    file_size: Optional[int] = None
    page_count: Optional[int] = None
    embed_status: str
    chunk_count: Optional[int] = None
    embedded_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BookResponse(BookListResponse):
    description: Optional[str] = None
    embed_error: Optional[str] = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmbedTriggerResponse(BaseModel):
    book_id: UUID
    embed_status: str
    message: str


class KeyInfo(BaseModel):
    index: int
    available: bool
    recovery_in: int  # seconds until recovery (0 if available)


class KeyStatusResponse(BaseModel):
    keys: List[KeyInfo]
    available_count: int
    total_count: int


class MessageResponse(BaseModel):
    message: str