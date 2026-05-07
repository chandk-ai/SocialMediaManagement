"""ApprovalPolicy aggregate — multi-step content approval (e.g.
legal → marketing → exec) with first-class delegation + vacation mode.

Replaces the single-reviewer flow encoded in `ReviewSession` for orgs that
need a chain of approvals (regulated industries, agencies, larger brands).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ..value_objects.ids import (
    ApprovalPolicyId,
    ApprovalRequestId,
    ApprovalStepId,
    OrgId,
    PlatformId,
    PostId,
    ReviewId,
    UserId,
    WorkflowId,
    new_id,
)


class ApprovalStepStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class ApprovalRequestStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ApprovalStep:
    """A single rung in the approval ladder."""
    id: ApprovalStepId
    name: str                                  # "legal", "marketing", "exec"
    approver_user_ids: tuple[UserId, ...]      # any one of these can sign off
    required_approvals: int = 1                # n-of-m sign-off
    delegate_user_ids: tuple[UserId, ...] = () # vacation backups
    review_channel: str = "in_app"             # "slack" | "email" | …
    optional: bool = False                     # if True, missing approver = skip
    timeout_minutes: int = 60 * 24             # auto-escalate after window
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class ApprovalPolicy:
    id: ApprovalPolicyId
    org_id: OrgId
    name: str
    description: str
    steps: list[ApprovalStep]
    applies_to_workflow_ids: tuple[WorkflowId, ...] = ()  # empty = all
    applies_to_platform_ids: tuple[PlatformId, ...] = ()  # empty = all
    require_unanimous_step: bool = False
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def covers(
        self,
        *,
        workflow_id: WorkflowId | None = None,
        platform_id: PlatformId | None = None,
    ) -> bool:
        wf_ok = (
            not self.applies_to_workflow_ids
            or (workflow_id is not None and workflow_id in self.applies_to_workflow_ids)
        )
        plat_ok = (
            not self.applies_to_platform_ids
            or (platform_id is not None and platform_id in self.applies_to_platform_ids)
        )
        return self.enabled and wf_ok and plat_ok

    @classmethod
    def create(
        cls,
        *,
        org_id: OrgId,
        name: str,
        description: str,
        steps: list[ApprovalStep],
        applies_to_workflow_ids: list[WorkflowId] | None = None,
        applies_to_platform_ids: list[PlatformId] | None = None,
        require_unanimous_step: bool = False,
    ) -> "ApprovalPolicy":
        if not steps:
            raise ValueError("Approval policy requires at least one step")
        return cls(
            id=ApprovalPolicyId(new_id()),
            org_id=org_id,
            name=name,
            description=description,
            steps=list(steps),
            applies_to_workflow_ids=tuple(applies_to_workflow_ids or ()),
            applies_to_platform_ids=tuple(applies_to_platform_ids or ()),
            require_unanimous_step=require_unanimous_step,
        )


@dataclass(slots=True)
class ApprovalDecision:
    step_id: ApprovalStepId
    approver_user_id: UserId
    approved: bool
    comment: str | None = None
    decided_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class ApprovalRequest:
    id: ApprovalRequestId
    org_id: OrgId
    policy_id: ApprovalPolicyId
    post_id: PostId
    workflow_id: WorkflowId | None
    review_id: ReviewId | None                  # link back to the kicker review
    current_step_index: int = 0
    decisions: list[ApprovalDecision] = field(default_factory=list)
    status: ApprovalRequestStatus = ApprovalRequestStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    extras: dict = field(default_factory=dict)

    def record_decision(
        self,
        *,
        step: ApprovalStep,
        approver_user_id: UserId,
        approved: bool,
        comment: str | None = None,
    ) -> ApprovalDecision:
        decision = ApprovalDecision(
            step_id=step.id,
            approver_user_id=approver_user_id,
            approved=approved,
            comment=comment,
        )
        self.decisions.append(decision)
        self.updated_at = datetime.utcnow()
        if self.status is ApprovalRequestStatus.PENDING:
            self.status = ApprovalRequestStatus.IN_PROGRESS
        return decision

    def step_decisions(self, step_id: ApprovalStepId) -> list[ApprovalDecision]:
        return [d for d in self.decisions if d.step_id == step_id]

    def is_step_complete(self, step: ApprovalStep) -> bool:
        approvals = [d for d in self.step_decisions(step.id) if d.approved]
        if step.required_approvals <= 0:
            return True
        return len(approvals) >= step.required_approvals

    def is_step_rejected(self, step: ApprovalStep) -> bool:
        return any(not d.approved for d in self.step_decisions(step.id))

    def advance(self, policy: ApprovalPolicy) -> None:
        """Walk forward through the policy. Mark complete / rejected when
        appropriate. Caller is expected to call after every decision."""
        while self.current_step_index < len(policy.steps):
            step = policy.steps[self.current_step_index]
            if self.is_step_rejected(step):
                self.status = ApprovalRequestStatus.REJECTED
                self.completed_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
                self.updated_at = self.completed_at
                return
            if self.is_step_complete(step) or step.optional:
                self.current_step_index += 1
                continue
            # waiting for more decisions on this step
            return
        # Walked off the end → all steps complete
        self.status = ApprovalRequestStatus.APPROVED
        self.completed_at = datetime.utcnow()
        self.updated_at = self.completed_at

    def cancel(self, reason: str | None = None) -> None:
        self.status = ApprovalRequestStatus.CANCELLED
        if reason:
            self.extras = {**self.extras, "cancellation_reason": reason}
        self.updated_at = datetime.utcnow()
        self.completed_at = self.updated_at

    @classmethod
    def create(
        cls,
        *,
        org_id: OrgId,
        policy_id: ApprovalPolicyId,
        post_id: PostId,
        workflow_id: WorkflowId | None = None,
        review_id: ReviewId | None = None,
    ) -> "ApprovalRequest":
        return cls(
            id=ApprovalRequestId(new_id()),
            org_id=org_id,
            policy_id=policy_id,
            post_id=post_id,
            workflow_id=workflow_id,
            review_id=review_id,
        )
