from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .common import APIModel


class SourceCreate(APIModel):
    plugin_name: str = Field(..., examples=["rss"])
    display_name: str
    config: dict = Field(default_factory=dict)


class SourceOut(APIModel):
    id: UUID
    plugin_name: str
    display_name: str
    is_active: bool
    config: dict
    last_fetched_at: datetime | None = None
    created_at: datetime
