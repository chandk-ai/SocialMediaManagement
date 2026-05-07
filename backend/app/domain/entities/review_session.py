"""ReviewSession — the human-in-the-loop checkpoint.

Created by the orchestrator when a workflow run produces drafts that need
approval (either because `workflow.config.require_human_approval=True` or
because Critique escalated). Bound to a review *channel* (e.g. WhatsApp)
and a *recipient* (the approver).

States:
    PENDING   → draft sent to reviewer, waiting for reply
    APPROVED  → reviewer said yes; orchestrator resumes and publishes
    REVISION_REQUESTED → reviewer sent feedback; agents revise and re-send
    REJECTED  → reviewer cancelled; run terminates
    EXPIRED   → no reply within the configured TTL
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..value_objects.ids import OrgId, ReviewId, RunId, WorkflowId, new_id


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ReviewSession:
    id: ReviewId
    org_id: OrgId
    workflow_id: WorkflowId
    run_id: RunId
    channel: str                  # plugin name of review channel — e.g. "whatsapp"
    recipient: str                # phone / IG handle / email of reviewer
    status: ReviewStatus = ReviewStatus.PENDING
    drafts_snapshot: list[dict] = field(default_factory=list)   # what was sent
    sent_message_ref: str | None = None     # external message ID to thread replies
    decision_at: datetime | None = None
    feedback: str | None = None             # raw text from reviewer (for revisions)
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def approve(self) -> None:
        self.status = ReviewStatus.APPROVED
        self.decision_at = datetime.utcnow()

    def request_revision(self, feedback: str) -> None:
        self.status = ReviewStatus.REVISION_REQUESTED
        self.feedback = feedback
        self.decision_at = datetime.utcnow()

    def reject(self, feedback: str | None = None) -> None:
        self.status = ReviewStatus.REJECTED
        self.feedback = feedback
        self.decision_at = datetime.utcnow()

    @classmethod
    def create(
        cls, *, org_id: OrgId, workflow_id: WorkflowId, run_id: RunId,
        channel: str, recipient: str, drafts_snapshot: list[dict],
        ttl_minutes: int = 60 * 24,
    ) -> "ReviewSession":
        return cls(
            id=ReviewId(new_id()),
            org_id=org_id,
            workflow_id=workflow_id,
            run_id=run_id,
            channel=channel,
            recipient=recipient,
            drafts_snapshot=drafts_snapshot,
            expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
        )
