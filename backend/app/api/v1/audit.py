"""Audit log read endpoint.

Writes happen in-line with the action they audit (see e.g. llm_keys.set_key
calling AuditLogService.record). This module exposes the timeline so the
``/audit`` page can render a real append-only log instead of synthesising
one client-side.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import current_user, get_audit_log_service
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId
from app.services.audit_log import AuditLogService

router = APIRouter()


def _require(svc: AuditLogService | None) -> AuditLogService:
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Audit log not available — backend is in memory mode.",
        )
    return svc


@router.get("/audit-logs")
async def list_audit_logs(
    limit: int = Query(200, ge=1, le=1000),
    days: int = Query(30, ge=1, le=365),
    action_prefix: str | None = None,
    resource_type: str | None = None,
    user: Principal = Depends(current_user),
    svc: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    s = _require(svc)
    since = datetime.utcnow() - timedelta(days=days)
    rows = await s.list_recent(
        OrgId(UUID(user.org_id)),
        limit=limit,
        action_prefix=action_prefix,
        resource_type=resource_type,
        since=since,
    )
    return {"events": rows}


@router.get("/audit-logs/verify")
async def verify_audit_chain(
    limit: int = Query(5000, ge=1, le=50000),
    user: Principal = Depends(current_user),
    svc: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    """Verify the org's tamper-evident audit chain.

    Returns ``chain_intact: true`` when every row's recomputed hash matches
    the stored ``row_hash`` AND each row's ``prev_hash`` matches the prior
    row's ``row_hash`` in walk order. A break indicates the row (or one
    before it) was modified or deleted directly in the database, bypassing
    the application. Useful for SOC2 / compliance review."""
    if not user.role.can_admin():
        raise HTTPException(status_code=403, detail="Admin role required")
    s = _require(svc)
    return await s.verify_chain(OrgId(UUID(user.org_id)), limit=limit)
