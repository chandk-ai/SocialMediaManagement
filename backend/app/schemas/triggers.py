from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .common import APIModel


class TriggerCreate(APIModel):
    workflow_id: UUID
    plugin_name: str = Field(..., examples=["whatsapp", "instagram", "webhook"])
    display_name: str
    config: dict = Field(default_factory=dict)
    allowed_senders: list[str] = Field(default_factory=list)
    review_channel: str | None = None
    review_recipient: str | None = None


class TriggerOut(APIModel):
    id: UUID
    workflow_id: UUID
    plugin_name: str
    display_name: str
    kind: str
    is_active: bool
    config: dict
    allowed_senders: list[str]
    review_channel: str | None
    review_recipient: str | None
    created_at: datetime
    last_fired_at: datetime | None = None


class WebhookAck(APIModel):
    received: int
    started: list[UUID] = Field(default_factory=list)
