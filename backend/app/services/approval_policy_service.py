"""ApprovalPolicyService — manages multi-step approval ladders and the
ApprovalRequest state machine that walks through them.

Wires into the existing review channel adapters so policy steps can be
delivered via Slack, email, WhatsApp, etc.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.core.logging import get_logger
from app.domain.entities.approval_policy import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRequestStatus,
    ApprovalStep,
)
from app.domain.entities.post import Post
from app.domain.value_objects.ids import (
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
from app.repositories.ports import (
    ApprovalPolicyRepository,
    ApprovalRequestRepository,
    PostRepository,
)

log = get_logger(__name__)


class ApprovalPolicyService:
    def __init__(
        self,
        policy_repo: ApprovalPolicyRepository,
        request_repo: ApprovalRequestRepository,
        post_repo: PostRepository,
    ) -> None:
        self.policy_repo = policy_repo
        self.request_repo = request_repo
        self.post_repo = post_repo

    # ── Policy CRUD ──────────────────────────────────────────────────
    async def create_policy(
        self,
        *,
        org_id: OrgId,
        name: str,
        description: str,
        steps: list[ApprovalStep],
        applies_to_workflow_ids: list[WorkflowId] | None = None,
        applies_to_platform_ids: list[PlatformId] | None = None,
        require_unanimous_step: bool = False,
    ) -> ApprovalPolicy:
        p = ApprovalPolicy.create(
            org_id=org_id,
            name=name,
            description=description,
            steps=steps,
            applies_to_workflow_ids=applies_to_workflow_ids,
            applies_to_platform_ids=applies_to_platform_ids,
            require_unanimous_step=require_unanimous_step,
        )
        return await self.policy_repo.add(p)

    async def list_policies(self, org_id: OrgId) -> list[ApprovalPolicy]:
        return await self.policy_repo.list(org_id)

    async def get_policy(
        self, org_id: OrgId, policy_id: ApprovalPolicyId,
    ) -> ApprovalPolicy:
        p = await self.policy_repo.get(org_id, policy_id)
        if not p:
            raise ValueError("approval policy not found")
        return p

    async def update_policy(
        self,
        *,
        org_id: OrgId,
        policy_id: ApprovalPolicyId,
        name: str | None = None,
        description: str | None = None,
        steps: list[ApprovalStep] | None = None,
        enabled: bool | None = None,
        applies_to_workflow_ids: list[WorkflowId] | None = None,
        applies_to_platform_ids: list[PlatformId] | None = None,
    ) -> ApprovalPolicy:
        p = await self.get_policy(org_id, policy_id)
        if name is not None:                p.name = name
        if description is not None:         p.description = description
        if steps is not None:               p.steps = list(steps)
        if enabled is not None:             p.enabled = enabled
        if applies_to_workflow_ids is not None:
            p.applies_to_workflow_ids = tuple(applies_to_workflow_ids)
        if applies_to_platform_ids is not None:
            p.applies_to_platform_ids = tuple(applies_to_platform_ids)
        p.updated_at = datetime.utcnow()
        return await self.policy_repo.update(p)

    async def delete_policy(
        self, org_id: OrgId, policy_id: ApprovalPolicyId,
    ) -> None:
        await self.policy_repo.delete(org_id, policy_id)

    # ── Policy resolution ────────────────────────────────────────────
    async def resolve_for(
        self,
        *,
        org_id: OrgId,
        workflow_id: WorkflowId | None = None,
        platform_id: PlatformId | None = None,
    ) -> ApprovalPolicy | None:
        """Pick the most-specific enabled policy that applies. Specificity
        order: (workflow_match + platform_match) > workflow_match > platform_match
        > org-default."""
        policies = await self.policy_repo.list(org_id)
        scored: list[tuple[int, ApprovalPolicy]] = []
        for p in policies:
            if not p.enabled:
                continue
            if not p.covers(workflow_id=workflow_id, platform_id=platform_id):
                continue
            score = 0
            if workflow_id and workflow_id in p.applies_to_workflow_ids:
                score += 2
            if platform_id and platform_id in p.applies_to_platform_ids:
                score += 1
            scored.append((score, p))
        if not scored:
            return None
        scored.sort(key=lambda t: t[0], reverse=True)
        return scored[0][1]

    # ── Request lifecycle ────────────────────────────────────────────
    async def open_request(
        self,
        *,
        org_id: OrgId,
        policy: ApprovalPolicy,
        post_id: PostId,
        workflow_id: WorkflowId | None = None,
        review_id: ReviewId | None = None,
    ) -> ApprovalRequest:
        req = ApprovalRequest.create(
            org_id=org_id,
            policy_id=policy.id,
            post_id=post_id,
            workflow_id=workflow_id,
            review_id=review_id,
        )
        return await self.request_repo.add(req)

    async def list_open(self, org_id: OrgId) -> list[ApprovalRequest]:
        return await self.request_repo.list_open(org_id)

    async def list_for_post(
        self, org_id: OrgId, post_id: PostId,
    ) -> list[ApprovalRequest]:
        return await self.request_repo.list_for_post(org_id, post_id)

    async def record_decision(
        self,
        *,
        org_id: OrgId,
        request_id: ApprovalRequestId,
        approver_user_id: UserId,
        approved: bool,
        comment: str | None = None,
    ) -> ApprovalRequest:
        req = await self.request_repo.get(org_id, request_id)
        if not req:
            raise ValueError("approval request not found")
        if req.status not in (
            ApprovalRequestStatus.PENDING,
            ApprovalRequestStatus.IN_PROGRESS,
        ):
            raise ValueError(f"cannot decide on {req.status.value} request")
        policy = await self.policy_repo.get(org_id, req.policy_id)
        if not policy:
            raise ValueError("policy missing for request")
        step = policy.steps[req.current_step_index]
        if not _approver_allowed(step, approver_user_id):
            raise ValueError("approver not authorised for this step")
        req.record_decision(
            step=step,
            approver_user_id=approver_user_id,
            approved=approved,
            comment=comment,
        )
        req.advance(policy)
        return await self.request_repo.update(req)

    async def cancel_request(
        self,
        *,
        org_id: OrgId,
        request_id: ApprovalRequestId,
        reason: str | None = None,
    ) -> ApprovalRequest:
        req = await self.request_repo.get(org_id, request_id)
        if not req:
            raise ValueError("approval request not found")
        req.cancel(reason)
        return await self.request_repo.update(req)

    async def current_step(
        self,
        *,
        org_id: OrgId,
        request_id: ApprovalRequestId,
    ) -> ApprovalStep | None:
        req = await self.request_repo.get(org_id, request_id)
        if not req:
            return None
        policy = await self.policy_repo.get(org_id, req.policy_id)
        if not policy or req.current_step_index >= len(policy.steps):
            return None
        return policy.steps[req.current_step_index]


def _approver_allowed(step: ApprovalStep, user_id: UserId) -> bool:
    if user_id in step.approver_user_ids:
        return True
    if user_id in step.delegate_user_ids:
        return True
    return False


def make_step(
    *,
    name: str,
    approver_user_ids: Iterable[UserId],
    required_approvals: int = 1,
    delegate_user_ids: Iterable[UserId] = (),
    review_channel: str = "in_app",
    optional: bool = False,
    timeout_minutes: int = 60 * 24,
    metadata: dict | None = None,
) -> ApprovalStep:
    """Convenience factory used by the API and tests."""
    return ApprovalStep(
        id=ApprovalStepId(new_id()),
        name=name,
        approver_user_ids=tuple(approver_user_ids),
        required_approvals=required_approvals,
        delegate_user_ids=tuple(delegate_user_ids),
        review_channel=review_channel,
        optional=optional,
        timeout_minutes=timeout_minutes,
        metadata=dict(metadata or {}),
    )
