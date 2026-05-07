"""Campaign aggregate — orchestrates a *sequence* of posts across one or
more platforms over a multi-day window with internal causality
(teaser → launch → recap, etc).

A `Campaign` is composed of `CampaignStep`s. Each step references the
workflow that should be executed at the step's `scheduled_for` moment, an
optional `target_selector` override, and a `directive` that is forwarded
into the agent pipeline as creative guidance ("this is the launch-day
post, build on the teaser from step 1").

The campaign itself does not produce posts — it is a thin orchestration
layer that the `CampaignService` walks step-by-step, deferring all heavy
lifting to the existing `WorkflowService`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..value_objects.ids import (
    CampaignId,
    CampaignStepId,
    OrgId,
    PlatformId,
    RunId,
    WorkflowId,
    new_id,
)
from ..value_objects.targeting import TargetSelector


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CampaignStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class CampaignKind(str, Enum):
    """Common campaign archetypes — Planner uses this to add the right
    creative beats to the prompt for each step."""
    PRODUCT_LAUNCH = "product_launch"
    EVENT = "event"
    THOUGHT_LEADERSHIP = "thought_leadership"
    PROMOTION = "promotion"
    DRIP = "drip"
    CUSTOM = "custom"


@dataclass(slots=True)
class CampaignStep:
    """A single beat in the campaign timeline."""
    id: CampaignStepId
    workflow_id: WorkflowId
    title: str
    directive: str                                  # natural-language brief
    scheduled_for: datetime
    target_selector: TargetSelector = field(default_factory=TargetSelector)
    depends_on: tuple[CampaignStepId, ...] = ()     # internal causality
    status: CampaignStepStatus = CampaignStepStatus.PENDING
    run_id: RunId | None = None                     # populated when run
    notes: str | None = None
    extras: dict = field(default_factory=dict)

    def mark_running(self, run_id: RunId | None = None) -> None:
        self.status = CampaignStepStatus.RUNNING
        if run_id is not None:
            self.run_id = run_id

    def mark_completed(self, run_id: RunId | None = None) -> None:
        self.status = CampaignStepStatus.COMPLETED
        if run_id is not None:
            self.run_id = run_id

    def mark_failed(self, message: str) -> None:
        self.status = CampaignStepStatus.FAILED
        self.notes = message

    def mark_skipped(self, reason: str) -> None:
        self.status = CampaignStepStatus.SKIPPED
        self.notes = reason

    @classmethod
    def create(
        cls,
        *,
        workflow_id: WorkflowId,
        title: str,
        directive: str,
        scheduled_for: datetime,
        target_selector: TargetSelector | None = None,
        depends_on: list[CampaignStepId] | None = None,
        extras: dict | None = None,
    ) -> "CampaignStep":
        return cls(
            id=CampaignStepId(new_id()),
            workflow_id=workflow_id,
            title=title,
            directive=directive,
            scheduled_for=scheduled_for,
            target_selector=target_selector or TargetSelector(),
            depends_on=tuple(depends_on or ()),
            extras=dict(extras or {}),
        )


@dataclass(slots=True)
class Campaign:
    id: CampaignId
    org_id: OrgId
    name: str
    description: str
    kind: CampaignKind
    starts_at: datetime
    ends_at: datetime
    steps: list[CampaignStep]
    status: CampaignStatus = CampaignStatus.DRAFT
    target_platform_ids: tuple[PlatformId, ...] = ()
    goal: str | None = None                          # "drive demo signups"
    success_metric: str | None = None                # "demo_signup_count"
    extras: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # ── lifecycle ─────────────────────────────────────────────────────
    def activate(self) -> None:
        if not self.steps:
            raise ValueError("Cannot activate a campaign with no steps")
        if self.ends_at <= self.starts_at:
            raise ValueError("Campaign ends_at must be after starts_at")
        self.status = CampaignStatus.SCHEDULED
        self.touch()

    def start(self) -> None:
        self.status = CampaignStatus.RUNNING
        self.touch()

    def pause(self) -> None:
        if self.status not in (CampaignStatus.SCHEDULED, CampaignStatus.RUNNING):
            raise ValueError(f"Cannot pause a campaign in status {self.status.value}")
        self.status = CampaignStatus.PAUSED
        self.touch()

    def cancel(self, reason: str | None = None) -> None:
        self.status = CampaignStatus.CANCELLED
        if reason:
            self.extras = {**self.extras, "cancellation_reason": reason}
        self.touch()

    def evaluate_completion(self) -> None:
        """Promote to COMPLETED/FAILED when every step is terminal."""
        terminal = {
            CampaignStepStatus.COMPLETED,
            CampaignStepStatus.SKIPPED,
            CampaignStepStatus.FAILED,
        }
        if not all(s.status in terminal for s in self.steps):
            return
        any_failed = any(
            s.status is CampaignStepStatus.FAILED for s in self.steps
        )
        any_completed = any(
            s.status is CampaignStepStatus.COMPLETED for s in self.steps
        )
        if any_failed and not any_completed:
            self.status = CampaignStatus.FAILED
        else:
            self.status = CampaignStatus.COMPLETED
        self.touch()

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()

    # ── selection helpers ─────────────────────────────────────────────
    def due_steps(self, now: datetime) -> list[CampaignStep]:
        """Pending steps whose schedule has elapsed and whose dependencies
        are completed."""
        completed = {
            s.id for s in self.steps if s.status is CampaignStepStatus.COMPLETED
        }
        out: list[CampaignStep] = []
        for s in self.steps:
            if s.status is not CampaignStepStatus.PENDING:
                continue
            if s.scheduled_for > now:
                continue
            if any(dep not in completed for dep in s.depends_on):
                continue
            out.append(s)
        return out

    @classmethod
    def create(
        cls,
        *,
        org_id: OrgId,
        name: str,
        description: str,
        kind: CampaignKind,
        starts_at: datetime,
        ends_at: datetime,
        steps: list[CampaignStep] | None = None,
        target_platform_ids: list[PlatformId] | None = None,
        goal: str | None = None,
        success_metric: str | None = None,
        extras: dict | None = None,
    ) -> "Campaign":
        return cls(
            id=CampaignId(new_id()),
            org_id=org_id,
            name=name,
            description=description,
            kind=kind,
            starts_at=starts_at,
            ends_at=ends_at,
            steps=list(steps or []),
            target_platform_ids=tuple(target_platform_ids or ()),
            goal=goal,
            success_metric=success_metric,
            extras=dict(extras or {}),
        )
