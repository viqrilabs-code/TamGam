# app/schemas/notification.py

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: UUID
    type: str
    title: str
    body: str
    extra_data: Optional[Dict[str, Any]] = None
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    unread_count: int
    total: int


class MessageResponse(BaseModel):
    message: str