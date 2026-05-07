"""HTTP routes for GDPR/CCPA data export & delete."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5, NAMESPACE_OID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import current_user, get_data_privacy_service
from app.core.security import Principal
from app.domain.entities.data_export import DataExportJob
from app.domain.value_objects.ids import DataExportJobId, OrgId, UserId
from app.services.data_privacy import DataPrivacyService

router = APIRouter()


class JobOut(BaseModel):
    id: UUID
    kind: str
    status: str
    download_url: str | None = None
    expires_at: datetime | None = None
    error: str | None = None
    counts: dict
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


def _job_out(j: DataExportJob) -> JobOut:
    return JobOut(
        id=UUID(str(j.id)),
        kind=j.kind.value,
        status=j.status.value,
        download_url=j.download_url,
        expires_at=j.expires_at,
        error=j.error,
        counts=dict(j.counts),
        created_at=j.created_at,
        updated_at=j.updated_at,
        completed_at=j.completed_at,
    )


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    user: Principal = Depends(current_user),
    svc: DataPrivacyService = Depends(get_data_privacy_service),
) -> list[JobOut]:
    items = await svc.list(OrgId(UUID(user.org_id)))
    return [_job_out(j) for j in items]


class RequestBody(BaseModel):
    kind: Literal["export", "delete"]
    grace_period_minutes: int | None = None


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def request_job(
    body: RequestBody,
    user: Principal = Depends(current_user),
    svc: DataPrivacyService = Depends(get_data_privacy_service),
) -> JobOut:
    if not user.role.can_admin():
        raise HTTPException(403, "Admin role required")
    requested_by = UserId(uuid5(NAMESPACE_OID, user.subject or user.email))
    if body.kind == "export":
        job = await svc.request_export(
            org_id=OrgId(UUID(user.org_id)),
            requested_by=requested_by,
        )
    else:
        job = await svc.request_delete(
            org_id=OrgId(UUID(user.org_id)),
            requested_by=requested_by,
            grace_period_minutes=body.grace_period_minutes or 60 * 24 * 7,
        )
    return _job_out(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: UUID,
    user: Principal = Depends(current_user),
    svc: DataPrivacyService = Depends(get_data_privacy_service),
) -> JobOut:
    if not user.role.can_admin():
        raise HTTPException(403, "Admin role required")
    try:
        j = await svc.cancel(
            org_id=OrgId(UUID(user.org_id)),
            job_id=DataExportJobId(job_id),
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return _job_out(j)


@router.post("/jobs/run-pending")
async def run_pending(
    user: Principal = Depends(current_user),
    svc: DataPrivacyService = Depends(get_data_privacy_service),
) -> dict:
    if not user.role.can_admin():
        raise HTTPException(403, "Admin role required")
    outcomes = await svc.run_pending()
    return {"outcomes": outcomes}
