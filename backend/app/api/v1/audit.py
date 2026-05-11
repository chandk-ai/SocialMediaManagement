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


@router.get("/audit-logs/paged")
async def list_audit_logs_paged(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None,
        description="Opaque cursor from the prior page's ``next_cursor``."),
    actor_id: str | None = Query(None,
        description="UUID — only events emitted by this user."),
    action: str | None = Query(None,
        description="Case-insensitive substring match on action."),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    since: datetime | None = Query(None,
        description="ISO8601 inclusive lower bound on occurred_at."),
    until: datetime | None = Query(None,
        description="ISO8601 exclusive upper bound on occurred_at."),
    user: Principal = Depends(current_user),
    svc: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    """Cursor-paged audit log read with filters.

    Reads a stable page of audit events, ordered newest-first. The
    cursor is opaque — clients pass back what the previous response's
    ``next_cursor`` returned. A null ``next_cursor`` means you've
    reached the end.

    Filters compose with AND. Bad UUIDs in ``actor_id`` /
    ``resource_id`` are silently ignored rather than 400'd because
    the UI sends filters from a typeahead that can momentarily emit
    junk."""
    s = _require(svc)
    return await s.list_paged(
        OrgId(UUID(user.org_id)),
        limit=limit, cursor=cursor,
        actor_id=actor_id, action=action,
        resource_type=resource_type, resource_id=resource_id,
        since=since, until=until,
    )


@router.get("/audit-logs/filters")
async def list_audit_filter_values(
    days: int = Query(90, ge=1, le=365,
        description="Look-back window in days for distinct values."),
    user: Principal = Depends(current_user),
    svc: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    """Populate filter dropdowns on the audit page. Returns the
    distinct values an org has actually produced in the recent
    window — so the UI doesn't offer filters that would always
    return zero results."""
    s = _require(svc)
    since = datetime.utcnow() - timedelta(days=days)
    return await s.distinct_filter_values(
        OrgId(UUID(user.org_id)), since=since,
    )


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
