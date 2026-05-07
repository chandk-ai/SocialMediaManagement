"""HTTP routes for content recycling / evergreen republishing."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_content_recycler
from app.core.security import Principal
from app.domain.entities.recycle_policy import RecyclePolicy, RecycleStrategy
from app.domain.value_objects.ids import (
    OrgId,
    PlatformId,
    RecyclePolicyId,
    WorkflowId,
)
from app.services.content_recycler import ContentRecyclerService

router = APIRouter()


class RecyclePolicyIn(BaseModel):
    name: str
    strategy: Literal[
        "repost_verbatim", "light_rewrite", "full_regen", "thread_from_top",
    ] = "light_rewrite"
    cooldown_days: int = 30
    max_recycle_count: int = 3
    cadence_days: int = 14
    min_engagement_score: float = 0.4
    workflow_ids: list[UUID] = Field(default_factory=list)
    platform_ids: list[UUID] = Field(default_factory=list)


class RecyclePolicyOut(BaseModel):
    id: UUID
    name: str
    strategy: str
    cooldown_days: int
    max_recycle_count: int
    cadence_days: int
    min_engagement_score: float
    workflow_ids: list[UUID]
    platform_ids: list[UUID]
    enabled: bool
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _policy_out(p: RecyclePolicy) -> RecyclePolicyOut:
    return RecyclePolicyOut(
        id=UUID(str(p.id)),
        name=p.name,
        strategy=p.strategy.value,
        cooldown_days=p.cooldown_days,
        max_recycle_count=p.max_recycle_count,
        cadence_days=p.cadence_days,
        min_engagement_score=p.min_engagement_score,
        workflow_ids=[UUID(str(w)) for w in p.workflow_ids],
        platform_ids=[UUID(str(pl)) for pl in p.platform_ids],
        enabled=p.enabled,
        last_run_at=p.last_run_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("/policies", response_model=list[RecyclePolicyOut])
async def list_policies(
    user: Principal = Depends(current_user),
    svc: ContentRecyclerService = Depends(get_content_recycler),
) -> list[RecyclePolicyOut]:
    items = await svc.list_policies(OrgId(UUID(user.org_id)))
    return [_policy_out(p) for p in items]


@router.post(
    "/policies",
    response_model=RecyclePolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_policy(
    body: RecyclePolicyIn,
    user: Principal = Depends(current_user),
    svc: ContentRecyclerService = Depends(get_content_recycler),
) -> RecyclePolicyOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    p = await svc.create_policy(
        org_id=OrgId(UUID(user.org_id)),
        name=body.name,
        strategy=RecycleStrategy(body.strategy),
        cooldown_days=body.cooldown_days,
        max_recycle_count=body.max_recycle_count,
        cadence_days=body.cadence_days,
        min_engagement_score=body.min_engagement_score,
        workflow_ids=[WorkflowId(w) for w in body.workflow_ids],
        platform_ids=[PlatformId(pl) for pl in body.platform_ids],
    )
    return _policy_out(p)


class RunBody(BaseModel):
    max_per_run: int = 3


@router.post("/policies/{policy_id}/run")
async def run_policy(
    policy_id: UUID,
    body: RunBody = RunBody(),
    user: Principal = Depends(current_user),
    svc: ContentRecyclerService = Depends(get_content_recycler),
) -> dict:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    outcomes = await svc.execute_policy(
        org_id=OrgId(UUID(user.org_id)),
        policy_id=RecyclePolicyId(policy_id),
        max_per_run=body.max_per_run,
    )
    return {
        "outcomes": [
            {
                "policy_id": str(o.policy_id),
                "candidate_post_id": str(o.candidate_post_id),
                "new_post_id": str(o.new_post_id) if o.new_post_id else None,
                "status": o.status,
                "detail": o.detail,
            }
            for o in outcomes
        ],
    }


@router.post("/policies/{policy_id}/disable", response_model=RecyclePolicyOut)
async def disable_policy(
    policy_id: UUID,
    user: Principal = Depends(current_user),
    svc: ContentRecyclerService = Depends(get_content_recycler),
) -> RecyclePolicyOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    p = await svc.disable(OrgId(UUID(user.org_id)), RecyclePolicyId(policy_id))
    return _policy_out(p)
