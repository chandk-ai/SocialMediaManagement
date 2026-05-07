from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .common import APIModel


class ReviewOut(APIModel):
    id: UUID
    run_id: UUID
    workflow_id: UUID
    channel: str
    recipient: str
    status: str
    drafts_snapshot: list[dict] = Field(default_factory=list)
    feedback: str | None = None
    decision_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime


class DecisionIn(APIModel):
    kind: str = Field(..., examples=["approve", "revise", "reject"])
    feedback: str = ""
