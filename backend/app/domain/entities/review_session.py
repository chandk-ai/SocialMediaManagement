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

Quorum (Niche #11):
    For group-chat channels (Telegram group with N admins), the trigger config
    can set ``quorum_required > 1`` so that ``K of N`` approvals are needed
    before the run resumes. A single REJECT vote is a hard veto — that's
    deliberate and matches how editorial boards actually work. A single
    REVISE vote also wins immediately (any one editor can request changes).
    Per-actor dedupe: tapping the same button twice counts once; changing
    your vote (Approve → Revise) updates in place.
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


VoteKind = str   # "approve" | "revise" | "reject"


@dataclass(slots=True)
class QuorumVote:
    """Single tap on the inline keyboard, recorded for quorum tallying."""
    actor_id: str
    actor_handle: str | None
    kind: VoteKind
    at: str                       # ISO 8601, kept as string for jsonb round-trip


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
    # ── Group quorum support ─────────────────────────────────────────
    quorum_required: int = 1                # 1 = first vote wins (default)
    quorum_votes: list[QuorumVote] = field(default_factory=list)

    # ── single-vote decisions (used by 1:1 channels) ─────────────────
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

    # ── quorum-aware vote recording ──────────────────────────────────
    def record_vote(
        self, *, actor_id: str, kind: VoteKind, actor_handle: str | None = None,
    ) -> "QuorumTally":
        """Idempotent per-actor: a second tap by the same user updates their
        existing vote in place. Returns the running tally so the caller can
        decide whether to promote the session."""
        kind = kind.lower()
        now = datetime.utcnow().isoformat()
        existing = next((v for v in self.quorum_votes if v.actor_id == actor_id), None)
        if existing is None:
            self.quorum_votes.append(QuorumVote(
                actor_id=actor_id, actor_handle=actor_handle, kind=kind, at=now,
            ))
        else:
            existing.kind = kind
            existing.actor_handle = actor_handle or existing.actor_handle
            existing.at = now
        return self.tally()

    def tally(self) -> "QuorumTally":
        approve = sum(1 for v in self.quorum_votes if v.kind == "approve")
        revise  = sum(1 for v in self.quorum_votes if v.kind == "revise")
        reject  = sum(1 for v in self.quorum_votes if v.kind == "reject")
        return QuorumTally(
            approve=approve, revise=revise, reject=reject,
            quorum_required=max(1, int(self.quorum_required)),
        )

    @classmethod
    def create(
        cls, *, org_id: OrgId, workflow_id: WorkflowId, run_id: RunId,
        channel: str, recipient: str, drafts_snapshot: list[dict],
        ttl_minutes: int = 60 * 24,
        quorum_required: int = 1,
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
            quorum_required=max(1, int(quorum_required)),
        )


@dataclass(frozen=True, slots=True)
class QuorumTally:
    approve: int
    revise: int
    reject: int
    quorum_required: int

    @property
    def reject_wins(self) -> bool:
        # A single reject vetoes the run — no waiting.
        return self.reject >= 1

    @property
    def revise_wins(self) -> bool:
        # Any editor asking for changes pulls the draft back immediately.
        return self.revise >= 1

    @property
    def approve_wins(self) -> bool:
        return self.approve >= self.quorum_required

    def short_label(self) -> str:
        """Compact human-readable label for surfacing in chat — e.g. 2/3."""
        return f"{self.approve}/{self.quorum_required}"
