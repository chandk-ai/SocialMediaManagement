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
    # Platform IDs the reviewer has excluded from publication. Empty by
    # default. The UI uses this on re-renders to keep the user's prior
    # exclusion state checked across page refreshes / repeat polls.
    excluded_platform_ids: list[str] = Field(default_factory=list)


class DecisionIn(APIModel):
    kind: str = Field(..., examples=["approve", "revise", "reject"])
    feedback: str = ""
    # When the reviewer approves, any platform IDs in this list will be
    # SKIPPED at publish time — the sibling Post for that target is
    # transitioned to CANCELLED with an audit entry. Persisted on the
    # ReviewSession so a downstream revise-loop preserves the choice.
    # Accepts UUID strings (the platform_id as returned in
    # drafts_snapshot entries). Empty list = publish everything.
    excluded_platform_ids: list[UUID] = Field(default_factory=list)
