import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, HttpUrl


class EventCreate(BaseModel):
    target_url: HttpUrl
    payload: dict[str, Any]


class EventOut(BaseModel):
    id: uuid.UUID
    target_url: str
    payload: dict[str, Any]
    status: str
    idempotency_key: str | None
    created_at: datetime

    class Config:
        from_attributes = True
