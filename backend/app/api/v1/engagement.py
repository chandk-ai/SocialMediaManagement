"""Engagement metrics — read endpoints + manual fetch trigger.

GET  /engagement/recent              latest snapshots, paginated
GET  /engagement/learnings/{dim}     ranked attribution per dimension
POST /engagement/posts/{id}/fetch    manually trigger a fetch
POST /engagement/aggregate           re-run rollup recompute (admin)

The frontend analytics page consumes /learnings/source, /learnings/hour,
/learnings/strategy, /learnings/platform.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import current_user, get_engagement_service
from app.core.security import Principal
from app.services.engagement.attribution import AttributionDimension

router = APIRouter()


@router.get("/engagement/recent")
async def recent_snapshots(
    limit: int = Query(100, ge=1, le=2000),
    user: Principal = Depends(current_user),
    svc=Depends(get_engagement_service),
) -> dict:
    rows = await svc.list_recent(user.org_id, limit=int(limit))
    return {"snapshots": rows, "total": len(rows)}


@router.get("/engagement/learnings/{dim}")
async def learnings_by_dimension(
    dim: str,
    limit: int = Query(50, ge=1, le=500),
    user: Principal = Depends(current_user),
    svc=Depends(get_engagement_service),
) -> dict:
    try:
        d = AttributionDimension(dim.lower())
    except ValueError:
        raise HTTPException(status_code=400,
                            detail=f"unknown dimension: {dim}. "
                                   f"Supported: {[e.value for e in AttributionDimension]}")
    rows = await svc.learnings(user.org_id, dim=d, limit=int(limit))
    return {"dimension": d.value,
            "results": [
                {"key": r.key, "avg_engagement": r.avg_engagement,
                 "p50": r.p50_engagement, "p90": r.p90_engagement,
                 "sample_size": r.sample_size}
                for r in rows
            ]}


@router.post("/engagement/posts/{post_id}/fetch")
async def manual_fetch(
    post_id: UUID,
    user: Principal = Depends(current_user),
    svc=Depends(get_engagement_service),
) -> dict:
    """Operator escape hatch — fetch metrics now without waiting for the
    scheduled T+1h / T+24h jobs. Useful for spot-checking or recovering
    from a missed fetch window."""
    snap = await svc.fetch_for_post(org_id=user.org_id,
                                      post_id=str(post_id))
    return snap or {"skipped": True}


@router.post("/engagement/aggregate")
async def force_aggregate(
    user: Principal = Depends(current_user),
    svc=Depends(get_engagement_service),
) -> dict:
    """Admin-triggered rollup recompute. The engagement.aggregate worker
    job runs this on a cadence; this endpoint lets the analytics page
    show fresh numbers right after a manual fetch."""
    if not user.role.can_admin():
        raise HTTPException(status_code=403, detail="Admin role required")
    n = await svc.aggregate(org_id=user.org_id)
    return {"rollup_rows": int(n)}
