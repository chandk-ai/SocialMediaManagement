"""HTTP routes for the performance-feedback learner."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_performance_learner
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId
from app.services.performance_learner import PerformanceLearner

router = APIRouter()


class WeightsOut(BaseModel):
    plugin_name: str
    weights: dict
    intercept: float
    sample_count: int
    r_squared: float
    updated_at: datetime


class FitOut(BaseModel):
    org_id: UUID
    fitted: list[WeightsOut]


@router.get("/weights/{plugin_name}", response_model=WeightsOut)
async def weights(
    plugin_name: str,
    user: Principal = Depends(current_user),
    svc: PerformanceLearner = Depends(get_performance_learner),
) -> WeightsOut:
    w = await svc.get_weights(OrgId(UUID(user.org_id)), plugin_name)
    return WeightsOut(
        plugin_name=w.plugin_name,
        weights=dict(w.weights),
        intercept=w.intercept,
        sample_count=w.sample_count,
        r_squared=w.r_squared,
        updated_at=w.updated_at,
    )


@router.post("/fit", response_model=FitOut)
async def fit(
    user: Principal = Depends(current_user),
    svc: PerformanceLearner = Depends(get_performance_learner),
) -> FitOut:
    org_id = OrgId(UUID(user.org_id))
    fitted = await svc.fit(org_id)
    return FitOut(
        org_id=UUID(str(org_id)),
        fitted=[
            WeightsOut(
                plugin_name=w.plugin_name,
                weights=dict(w.weights),
                intercept=w.intercept,
                sample_count=w.sample_count,
                r_squared=w.r_squared,
                updated_at=w.updated_at,
            )
            for w in fitted
        ],
    )
