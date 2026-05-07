from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..value_objects.ids import OrgId, RunId, TriggerId, WorkflowId, new_id


class RunStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    EXECUTING = "executing"
    EVALUATING = "evaluating"
    CRITIQUING = "critiquing"
    AWAITING_REVIEW = "awaiting_review"   # paused for a human decision
    PUBLISHING = "publishing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentTraceEvent:
    """One step in the audit-grade trace of an agent's decisions."""
    agent: str                  # planner / executor / evaluator / critique / trigger / review
    event: str                  # e.g. "draft_generated", "revision_requested"
    payload: dict[str, Any]
    occurred_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class WorkflowRun:
    id: RunId
    org_id: OrgId
    workflow_id: WorkflowId
    status: RunStatus = RunStatus.QUEUED
    revision_count: int = 0
    trace: list[AgentTraceEvent] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    # Source of this run — links back to the Trigger that fired it (None = manual API).
    trigger_id: TriggerId | None = None
    # User-supplied directive (e.g. "post about our Q4 launch event in SF").
    # Empty for purely source-driven runs.
    directive: str = ""
    # The sender's identifier (phone, IG account, email) when triggered via a channel.
    initiator: str | None = None

    def append(self, event: AgentTraceEvent) -> None:
        self.trace.append(event)

    def transition(self, new: RunStatus) -> None:
        self.status = new
        if new is RunStatus.PLANNING and self.started_at is None:
            self.started_at = datetime.utcnow()
        if new in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            self.finished_at = datetime.utcnow()

    @classmethod
    def create(
        cls, *, org_id: OrgId, workflow_id: WorkflowId,
        trigger_id: TriggerId | None = None, directive: str = "",
        initiator: str | None = None,
    ) -> "WorkflowRun":
        return cls(
            id=RunId(new_id()), org_id=org_id, workflow_id=workflow_id,
            trigger_id=trigger_id, directive=directive, initiator=initiator,
        )
