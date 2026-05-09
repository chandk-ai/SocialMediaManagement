"""Jobs API — admin visibility for the durable run engine.

Read endpoints:
    GET  /jobs                  list with filters (kind, status, run_id)
    GET  /jobs/{id}             single job detail
    GET  /jobs/queue/health     headline counts (queued / running / dead)

Write endpoints (admin only):
    POST   /jobs/{id}/cancel    cancel a queued or running job
    POST   /jobs/{id}/retry     reset a dead job back to queued
    POST   /jobs/sweep          force an orphan-recovery sweep

The dashboard at /admin/jobs uses these.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import current_user, get_audit_log_service, get_job_queue
from app.core.security import Principal
from app.services.audit_log import AuditLogService
from app.services.jobs.queue import JobNotFound, JobQueue, JobStatus

router = APIRouter()


def _ser(j) -> dict[str, Any]:
    return {
        "id": j.id, "org_id": j.org_id, "kind": j.kind,
        "status": j.status.value if hasattr(j.status, "value") else str(j.status),
        "attempt": j.attempt, "max_attempts": j.max_attempts,
        "priority": j.priority,
        "scheduled_for": j.scheduled_for.isoformat() if j.scheduled_for else None,
        "run_id": j.run_id, "claimed_by": j.claimed_by,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "error": j.error, "result": j.result,
        "idempotency_key": j.idempotency_key,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        "payload_keys": list((j.payload or {}).keys()),
    }


@router.get("/jobs")
async def list_jobs(
    status: str | None = Query(None),
    kind: str | None = Query(None),
    run_id: UUID | None = Query(None),
    limit: int = Query(100, ge=1, le=2000),
    user: Principal = Depends(current_user),
    queue: JobQueue = Depends(get_job_queue),
) -> dict:
    rows = await queue.list_for_org(
        user.org_id, status=status, kind=kind,
        run_id=str(run_id) if run_id else None,
        limit=int(limit),
    )
    counts = {"queued": 0, "running": 0, "succeeded": 0,
              "failed": 0, "dead": 0, "cancelled": 0}
    for r in rows:
        st = r.status.value if hasattr(r.status, "value") else str(r.status)
        if st in counts:
            counts[st] += 1
    return {"jobs": [_ser(r) for r in rows],
            "counts": counts, "total": len(rows)}


@router.get("/jobs/queue/health")
async def queue_health(
    user: Principal = Depends(current_user),
    queue: JobQueue = Depends(get_job_queue),
) -> dict:
    """Counts grouped by status — for a banner / status page."""
    rows = await queue.list_for_org(user.org_id, limit=2000)
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for r in rows:
        st = r.status.value if hasattr(r.status, "value") else str(r.status)
        by_status[st] = by_status.get(st, 0) + 1
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    return {"by_status": by_status, "by_kind": by_kind,
            "total": len(rows)}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: UUID,
    user: Principal = Depends(current_user),
    queue: JobQueue = Depends(get_job_queue),
) -> dict:
    j = await queue.get(str(job_id))
    if j is None or j.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="job not found")
    out = _ser(j)
    out["payload"] = j.payload  # full payload on detail call
    return out


class CancelBody(BaseModel):
    reason: str | None = None


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: UUID,
    body: CancelBody | None = None,
    user: Principal = Depends(current_user),
    queue: JobQueue = Depends(get_job_queue),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    if not user.role.can_admin():
        raise HTTPException(status_code=403, detail="Admin role required")
    j = await queue.get(str(job_id))
    if j is None or j.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        await queue.cancel(str(job_id))
    except JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")
    if audit:
        await audit.record(
            org_id=user.org_id, action="job.cancel",
            resource_type="job", resource_id=job_id,
            after={"reason": (body.reason if body else None)},
        )
    return {"id": str(job_id), "status": JobStatus.CANCELLED.value}


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: UUID,
    user: Principal = Depends(current_user),
    queue: JobQueue = Depends(get_job_queue),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    """Reset a dead/cancelled job back to queued so the worker
    re-claims it. Bumps max_attempts so it gets fresh retries."""
    if not user.role.can_admin():
        raise HTTPException(status_code=403, detail="Admin role required")
    j = await queue.get(str(job_id))
    if j is None or j.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="job not found")
    if j.status not in (JobStatus.DEAD, JobStatus.CANCELLED, JobStatus.FAILED):
        raise HTTPException(status_code=400,
                            detail=f"cannot retry from {j.status.value}")
    # Re-enqueue with same payload + attempt budget.
    await queue.enqueue(
        j.kind, j.org_id, j.payload, run_id=j.run_id,
        priority=j.priority, max_attempts=j.max_attempts,
        idempotency_key=f"retry:{j.id}",
    )
    if audit:
        await audit.record(
            org_id=user.org_id, action="job.retry",
            resource_type="job", resource_id=job_id,
        )
    return {"id": str(job_id), "status": "requeued"}


@router.post("/jobs/sweep")
async def sweep_orphans(
    stale_seconds: int = Query(600, ge=30, le=86400),
    user: Principal = Depends(current_user),
    queue: JobQueue = Depends(get_job_queue),
) -> dict:
    if not user.role.can_admin():
        raise HTTPException(status_code=403, detail="Admin role required")
    n = await queue.sweep_orphans(float(stale_seconds))
    return {"recovered": int(n)}
