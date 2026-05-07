"""HTTP routes for A/B/n experiments."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_experiment_service
from app.core.security import Principal
from app.domain.entities.experiment import (
    AllocationKind,
    Experiment,
    Variant,
    VariantStatus,
)
from app.domain.value_objects.ids import (
    ExperimentId,
    OrgId,
    PlatformId,
    RunId,
    WorkflowId,
)
from app.services.experiment_service import ExperimentService

router = APIRouter()


class VariantIn(BaseModel):
    label: str
    text: str
    hashtags: list[str] = Field(default_factory=list)
    media_briefs: list[str] = Field(default_factory=list)
    weight: float = 1.0
    extras: dict = Field(default_factory=dict)


class VariantOut(BaseModel):
    id: UUID
    label: str
    text: str
    weight: float
    status: str
    metric_value: float | None = None
    notes: str | None = None
    post_id: UUID | None = None


class ExperimentCreate(BaseModel):
    workflow_id: UUID
    platform_id: UUID
    hypothesis: str
    metric: str = "engagement_rate"
    settling_minutes: int = 60 * 24
    allocation: Literal["equal", "holdout", "bandit"] = "equal"
    variants: list[VariantIn]
    extras: dict = Field(default_factory=dict)


class ExperimentOut(BaseModel):
    id: UUID
    workflow_id: UUID
    platform_id: UUID
    hypothesis: str
    metric: str
    settling_minutes: int
    allocation: str
    status: str
    started_at: datetime | None = None
    settled_at: datetime | None = None
    winner_variant_id: UUID | None = None
    variants: list[VariantOut]
    created_at: datetime
    updated_at: datetime


def _variant_out(v: Variant) -> VariantOut:
    return VariantOut(
        id=UUID(str(v.id)),
        label=v.label,
        text=v.text,
        weight=v.weight,
        status=v.status.value,
        metric_value=v.metric_value,
        notes=v.notes,
        post_id=UUID(str(v.post_id)) if v.post_id else None,
    )


def _experiment_out(e: Experiment) -> ExperimentOut:
    return ExperimentOut(
        id=UUID(str(e.id)),
        workflow_id=UUID(str(e.workflow_id)),
        platform_id=UUID(str(e.platform_id)),
        hypothesis=e.hypothesis,
        metric=e.metric,
        settling_minutes=e.settling_minutes,
        allocation=e.allocation.value,
        status=e.status.value,
        started_at=e.started_at,
        settled_at=e.settled_at,
        winner_variant_id=UUID(str(e.winner_variant_id))
        if e.winner_variant_id else None,
        variants=[_variant_out(v) for v in e.variants],
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


@router.get("", response_model=list[ExperimentOut])
async def list_experiments(
    status_filter: str | None = None,
    user: Principal = Depends(current_user),
    svc: ExperimentService = Depends(get_experiment_service),
) -> list[ExperimentOut]:
    items = await svc.list(OrgId(UUID(user.org_id)), status=status_filter)
    return [_experiment_out(e) for e in items]


@router.post(
    "", response_model=ExperimentOut, status_code=status.HTTP_201_CREATED,
)
async def create_experiment(
    body: ExperimentCreate,
    user: Principal = Depends(current_user),
    svc: ExperimentService = Depends(get_experiment_service),
) -> ExperimentOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    variants = [
        Variant.create(
            label=v.label,
            text=v.text,
            hashtags=v.hashtags,
            media_briefs=v.media_briefs,
            weight=v.weight,
            extras=v.extras,
        )
        for v in body.variants
    ]
    e = await svc.create(
        org_id=OrgId(UUID(user.org_id)),
        workflow_id=WorkflowId(body.workflow_id),
        platform_id=PlatformId(body.platform_id),
        hypothesis=body.hypothesis,
        variants=variants,
        metric=body.metric,
        settling_minutes=body.settling_minutes,
        allocation=AllocationKind(body.allocation),
        extras=body.extras,
    )
    return _experiment_out(e)


class StartBody(BaseModel):
    run_id: UUID


@router.post("/{experiment_id}/start", response_model=ExperimentOut)
async def start_experiment(
    experiment_id: UUID,
    body: StartBody,
    user: Principal = Depends(current_user),
    svc: ExperimentService = Depends(get_experiment_service),
) -> ExperimentOut:
    if not user.role.can_edit():
        raise HTTPException(403, "Editor role required")
    try:
        e = await svc.start(
            org_id=OrgId(UUID(user.org_id)),
            experiment_id=ExperimentId(experiment_id),
            run_id=RunId(body.run_id),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _experiment_out(e)


@router.post("/{experiment_id}/collect-metrics", response_model=ExperimentOut)
async def collect_metrics(
    experiment_id: UUID,
    user: Principal = Depends(current_user),
    svc: ExperimentService = Depends(get_experiment_service),
) -> ExperimentOut:
    e = await svc.collect_metrics(
        org_id=OrgId(UUID(user.org_id)),
        experiment_id=ExperimentId(experiment_id),
    )
    return _experiment_out(e)


@router.post("/{experiment_id}/settle", response_model=ExperimentOut)
async def settle_experiment(
    experiment_id: UUID,
    user: Principal = Depends(current_user),
    svc: ExperimentService = Depends(get_experiment_service),
) -> ExperimentOut:
    e = await svc.maybe_settle(
        org_id=OrgId(UUID(user.org_id)),
        experiment_id=ExperimentId(experiment_id),
    )
    return _experiment_out(e)


@router.post("/{experiment_id}/cancel", response_model=ExperimentOut)
async def cancel_experiment(
    experiment_id: UUID,
    reason: str | None = None,
    user: Principal = Depends(current_user),
    svc: ExperimentService = Depends(get_experiment_service),
) -> ExperimentOut:
    e = await svc.cancel(
        OrgId(UUID(user.org_id)), ExperimentId(experiment_id), reason=reason,
    )
    return _experiment_out(e)
