from __future__ import annotations

from datetime import datetime
from uuid import UUID

from .common import APIModel


class PostOut(APIModel):
    id: UUID
    workflow_id: UUID
    run_id: UUID
    platform_id: UUID
    text: str
    hashtags: list[str]
    status: str
    scheduled_for: datetime | None
    published_at: datetime | None
    external_post_id: str | None
    error: str | None
    created_at: datetime
