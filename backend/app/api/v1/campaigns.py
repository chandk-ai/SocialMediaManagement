"""HTTP routes for the Campaign aggregate."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_campaign_service
from app.core.security import Principal
from app.domain.entities.campaign import (
    Campaign,
    CampaignKind,
    CampaignStep,
    CampaignStepStatus,
)
from app.domain.value_objects.ids import (
    CampaignId,
    CampaignStepId,
    OrgId,
    PlatformId,
    WorkflowId,
    new_id,
)
from app.domain.value_objects.targeting import TargetSelector
from app.services.campaign_service import CampaignService

router = APIRouter()


# ── Pydantic IO ──────────────────────────────────────────────────────────
class CampaignStepIn(BaseModel):
    workflow_id: UUID
    title: str
    directive: str
    scheduled_for: datetime
    target_platform_ids: list[UUID] = Field(default_factory=list)
    depends_on: list[UUID] = Field(default_factory=list)
    extras: dict = Field(default_factory=dict)


class CampaignStepOut(BaseModel):
    id: UUID
    workflow_id: UUID
    title: str
    directive: str
    scheduled_for: datetime
    status: str
    run_id: UUID | None = None
    notes: str | None = None
    depends_on: list[UUID]
    extras: dict


class CampaignCreate(BaseModel):
    name: str
    description: str
    kind: Literal[
        "product_launch", "event", "thought_leadership",
        "promotion", "drip", "custom",
    ] = "custom"
    starts_at: datetime
    ends_at: datetime
    goal: str | None = None
    success_metric: str | None = None
    target_platform_ids: list[UUID] = Field(default_factory=list)
    steps: list[CampaignStepIn] = Field(default_factory=list)
    extras: dict = Field(default_factory=dict)


class CampaignOut(BaseModel):
    id: UUID
    name: str
    description: str
    kind: str
    status: str
    starts_at: datetime
    ends_at: datetime
    goal: str | None = None
    success_metric: str | None = None
    target_platform_ids: list[UUID]
    steps: list[CampaignStepOut]
    created_at: datetime
    updated_at: datetime


# ── helpers ───────────────────────────────────────────────────────────────
def _step_in_to_domain(s: CampaignStepIn) -> CampaignStep:
    selector = TargetSelector(
        explicit_platform_ids=tuple(PlatformId(p) for p in s.target_platform_ids),
    )
    return CampaignStep.create(
        workflow_id=WorkflowId(s.workflow_id),
        title=s.title,
        directive=s.directive,
        scheduled_for=s.scheduled_for,
        target_selector=selector,
        depends_on=[CampaignStepId(d) for d in s.depends_on],
        extras=s.extras,
    )


def _step_out(s: CampaignStep) -> CampaignStepOut:
    return CampaignStepOut(
        id=UUID(str(s.id)),
        workflow_id=UUID(str(s.workflow_id)),
        title=s.title,
        directive=s.directive,
        scheduled_for=s.scheduled_for,
        status=s.status.value,
        run_id=UUID(str(s.run_id)) if s.run_id else None,
        notes=s.notes,
        depends_on=[UUID(str(d)) for d in s.depends_on],
        extras=dict(s.extras),
    )


def _campaign_out(c: Campaign) -> CampaignOut:
    return CampaignOut(
        id=UUID(str(c.id)),
        name=c.name,
        description=c.description,
        kind=c.kind.value,
        status=c.status.value,
        starts_at=c.starts_at,
        ends_at=c.ends_at,
        goal=c.goal,
        success_metric=c.success_metric,
        target_platform_ids=[UUID(str(p)) for p in c.target_platform_ids],
        steps=[_step_out(s) for s in c.steps],
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


# ── Routes ────────────────────────────────────────────────────────────────
@router.get("", response_model=list[CampaignOut])
async def list_campaigns(
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> list[CampaignOut]:
    items = await svc.list(OrgId(UUID(user.org_id)))
    return [_campaign_out(c) for c in items]


@router.post(
    "", response_model=CampaignOut, status_code=status.HTTP_201_CREATED,
)
async def create_campaign(
    body: CampaignCreate,
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> CampaignOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    campaign = await svc.create(
        org_id=OrgId(UUID(user.org_id)),
        name=body.name,
        description=body.description,
        kind=CampaignKind(body.kind),
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        steps=[_step_in_to_domain(s) for s in body.steps],
        target_platform_ids=[PlatformId(p) for p in body.target_platform_ids],
        goal=body.goal,
        success_metric=body.success_metric,
        extras=body.extras,
    )
    return _campaign_out(campaign)


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: UUID,
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> CampaignOut:
    try:
        c = await svc.get(OrgId(UUID(user.org_id)), CampaignId(campaign_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return _campaign_out(c)


@router.post("/{campaign_id}/steps", response_model=CampaignOut)
async def add_step(
    campaign_id: UUID,
    body: CampaignStepIn,
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> CampaignOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    c = await svc.add_step(
        org_id=OrgId(UUID(user.org_id)),
        campaign_id=CampaignId(campaign_id),
        step=_step_in_to_domain(body),
    )
    return _campaign_out(c)


@router.post("/{campaign_id}/activate", response_model=CampaignOut)
async def activate_campaign(
    campaign_id: UUID,
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> CampaignOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    try:
        c = await svc.activate(OrgId(UUID(user.org_id)), CampaignId(campaign_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _campaign_out(c)


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(
    campaign_id: UUID,
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> CampaignOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    try:
        c = await svc.pause(OrgId(UUID(user.org_id)), CampaignId(campaign_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _campaign_out(c)


class CancelBody(BaseModel):
    reason: str | None = None


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
async def cancel_campaign(
    campaign_id: UUID,
    body: CancelBody = CancelBody(),
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> CampaignOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    c = await svc.cancel(
        OrgId(UUID(user.org_id)), CampaignId(campaign_id), reason=body.reason,
    )
    return _campaign_out(c)


@router.post("/run-due")
async def run_due_steps(
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> dict:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    outcomes = await svc.execute_due_steps()
    return {
        "outcomes": [
            {
                "campaign_id": str(cid),
                "step_id": str(sid),
                "outcome": outcome,
            }
            for (cid, sid, outcome) in outcomes
        ],
    }


@router.delete(
    "/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_campaign(
    campaign_id: UUID,
    user: Principal = Depends(current_user),
    svc: CampaignService = Depends(get_campaign_service),
) -> None:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    await svc.delete(OrgId(UUID(user.org_id)), CampaignId(campaign_id))
