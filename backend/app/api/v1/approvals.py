"""HTTP routes for multi-step approval policies + requests."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_approval_policy_service
from app.core.security import Principal
from app.domain.entities.approval_policy import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalStep,
)
from app.domain.value_objects.ids import (
    ApprovalPolicyId,
    ApprovalRequestId,
    OrgId,
    PlatformId,
    PostId,
    UserId,
    WorkflowId,
)
from app.services.approval_policy_service import (
    ApprovalPolicyService,
    make_step,
)

router = APIRouter()


class ApprovalStepIn(BaseModel):
    name: str
    approver_user_ids: list[UUID] = Field(default_factory=list)
    delegate_user_ids: list[UUID] = Field(default_factory=list)
    required_approvals: int = 1
    review_channel: str = "in_app"
    optional: bool = False
    timeout_minutes: int = 60 * 24
    metadata: dict = Field(default_factory=dict)


class ApprovalStepOut(BaseModel):
    id: UUID
    name: str
    approver_user_ids: list[UUID]
    delegate_user_ids: list[UUID]
    required_approvals: int
    review_channel: str
    optional: bool
    timeout_minutes: int


class ApprovalPolicyIn(BaseModel):
    name: str
    description: str
    steps: list[ApprovalStepIn]
    applies_to_workflow_ids: list[UUID] = Field(default_factory=list)
    applies_to_platform_ids: list[UUID] = Field(default_factory=list)
    require_unanimous_step: bool = False


class ApprovalPolicyOut(BaseModel):
    id: UUID
    name: str
    description: str
    enabled: bool
    steps: list[ApprovalStepOut]
    applies_to_workflow_ids: list[UUID]
    applies_to_platform_ids: list[UUID]
    require_unanimous_step: bool
    created_at: datetime
    updated_at: datetime


class ApprovalDecisionIn(BaseModel):
    request_id: UUID
    approver_user_id: UUID
    approved: bool
    comment: str | None = None


class ApprovalRequestOut(BaseModel):
    id: UUID
    policy_id: UUID
    post_id: UUID
    workflow_id: UUID | None = None
    current_step_index: int
    status: str
    decisions: list[dict]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


def _step_in(s: ApprovalStepIn) -> ApprovalStep:
    return make_step(
        name=s.name,
        approver_user_ids=[UserId(u) for u in s.approver_user_ids],
        required_approvals=s.required_approvals,
        delegate_user_ids=[UserId(u) for u in s.delegate_user_ids],
        review_channel=s.review_channel,
        optional=s.optional,
        timeout_minutes=s.timeout_minutes,
        metadata=s.metadata,
    )


def _step_out(s: ApprovalStep) -> ApprovalStepOut:
    return ApprovalStepOut(
        id=UUID(str(s.id)),
        name=s.name,
        approver_user_ids=[UUID(str(u)) for u in s.approver_user_ids],
        delegate_user_ids=[UUID(str(u)) for u in s.delegate_user_ids],
        required_approvals=s.required_approvals,
        review_channel=s.review_channel,
        optional=s.optional,
        timeout_minutes=s.timeout_minutes,
    )


def _policy_out(p: ApprovalPolicy) -> ApprovalPolicyOut:
    return ApprovalPolicyOut(
        id=UUID(str(p.id)),
        name=p.name,
        description=p.description,
        enabled=p.enabled,
        steps=[_step_out(s) for s in p.steps],
        applies_to_workflow_ids=[UUID(str(w)) for w in p.applies_to_workflow_ids],
        applies_to_platform_ids=[UUID(str(pl)) for pl in p.applies_to_platform_ids],
        require_unanimous_step=p.require_unanimous_step,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _request_out(r: ApprovalRequest) -> ApprovalRequestOut:
    return ApprovalRequestOut(
        id=UUID(str(r.id)),
        policy_id=UUID(str(r.policy_id)),
        post_id=UUID(str(r.post_id)),
        workflow_id=UUID(str(r.workflow_id)) if r.workflow_id else None,
        current_step_index=r.current_step_index,
        status=r.status.value,
        decisions=[
            {
                "step_id": str(d.step_id),
                "approver_user_id": str(d.approver_user_id),
                "approved": d.approved,
                "comment": d.comment,
                "decided_at": d.decided_at.isoformat() + "Z",
            }
            for d in r.decisions
        ],
        created_at=r.created_at,
        updated_at=r.updated_at,
        completed_at=r.completed_at,
    )


# ── Routes ────────────────────────────────────────────────────────────────
@router.get("/policies", response_model=list[ApprovalPolicyOut])
async def list_policies(
    user: Principal = Depends(current_user),
    svc: ApprovalPolicyService = Depends(get_approval_policy_service),
) -> list[ApprovalPolicyOut]:
    items = await svc.list_policies(OrgId(UUID(user.org_id)))
    return [_policy_out(p) for p in items]


@router.post(
    "/policies",
    response_model=ApprovalPolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_policy(
    body: ApprovalPolicyIn,
    user: Principal = Depends(current_user),
    svc: ApprovalPolicyService = Depends(get_approval_policy_service),
) -> ApprovalPolicyOut:
    if not user.role.can_admin():
        raise HTTPException(403, "Admin role required")
    if not body.steps:
        raise HTTPException(400, "At least one step required")
    p = await svc.create_policy(
        org_id=OrgId(UUID(user.org_id)),
        name=body.name,
        description=body.description,
        steps=[_step_in(s) for s in body.steps],
        applies_to_workflow_ids=[WorkflowId(w) for w in body.applies_to_workflow_ids],
        applies_to_platform_ids=[PlatformId(pl) for pl in body.applies_to_platform_ids],
        require_unanimous_step=body.require_unanimous_step,
    )
    return _policy_out(p)


@router.delete(
    "/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_policy(
    policy_id: UUID,
    user: Principal = Depends(current_user),
    svc: ApprovalPolicyService = Depends(get_approval_policy_service),
) -> None:
    if not user.role.can_admin():
        raise HTTPException(403, "Admin role required")
    await svc.delete_policy(OrgId(UUID(user.org_id)), ApprovalPolicyId(policy_id))


@router.get("/requests/open", response_model=list[ApprovalRequestOut])
async def list_open_requests(
    user: Principal = Depends(current_user),
    svc: ApprovalPolicyService = Depends(get_approval_policy_service),
) -> list[ApprovalRequestOut]:
    items = await svc.list_open(OrgId(UUID(user.org_id)))
    return [_request_out(r) for r in items]


@router.get("/requests/by-post/{post_id}", response_model=list[ApprovalRequestOut])
async def list_for_post(
    post_id: UUID,
    user: Principal = Depends(current_user),
    svc: ApprovalPolicyService = Depends(get_approval_policy_service),
) -> list[ApprovalRequestOut]:
    items = await svc.list_for_post(OrgId(UUID(user.org_id)), PostId(post_id))
    return [_request_out(r) for r in items]


@router.post("/requests/decide", response_model=ApprovalRequestOut)
async def decide(
    body: ApprovalDecisionIn,
    user: Principal = Depends(current_user),
    svc: ApprovalPolicyService = Depends(get_approval_policy_service),
) -> ApprovalRequestOut:
    try:
        r = await svc.record_decision(
            org_id=OrgId(UUID(user.org_id)),
            request_id=ApprovalRequestId(body.request_id),
            approver_user_id=UserId(body.approver_user_id),
            approved=body.approved,
            comment=body.comment,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _request_out(r)


class CancelBody(BaseModel):
    reason: str | None = None


@router.post("/requests/{request_id}/cancel", response_model=ApprovalRequestOut)
async def cancel(
    request_id: UUID,
    body: CancelBody = CancelBody(),
    user: Principal = Depends(current_user),
    svc: ApprovalPolicyService = Depends(get_approval_policy_service),
) -> ApprovalRequestOut:
    r = await svc.cancel_request(
        org_id=OrgId(UUID(user.org_id)),
        request_id=ApprovalRequestId(request_id),
        reason=body.reason,
    )
    return _request_out(r)
