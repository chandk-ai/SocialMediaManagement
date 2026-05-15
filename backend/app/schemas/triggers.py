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


class TriggerUpdate(APIModel):
    """Partial update. Any field left as ``None`` is preserved.

    ``config`` is REPLACED wholesale when present — there's no merge
    semantics because trigger configs are plugin-specific JSON shapes
    with required fields, and a merge could leave a half-populated
    config that crashes the adapter at fire-time. The frontend always
    sends the full intended config.

    ``plugin_name`` and ``workflow_id`` are intentionally NOT editable
    here — changing the plugin would invalidate the existing config
    schema, and rebinding to a different workflow is a separate
    workflow-editor concern. Delete + recreate if you need either.
    """
    display_name: str | None = None
    config: dict | None = None
    allowed_senders: list[str] | None = None
    review_channel: str | None = None
    review_recipient: str | None = None
    is_active: bool | None = None


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
