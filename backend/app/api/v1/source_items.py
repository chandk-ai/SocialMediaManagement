"""Source-item registry endpoints — list, tag, reset.

The selection layer (Niche #101) writes to ``smms.source_items`` to track
what each source has yielded and what's been consumed. This module
exposes that ledger to admins so they can:

* See recent items per source (the "consumption history" panel on the
  source detail page)
* Tag items (``ready`` / ``hold`` / ``pinned`` / arbitrary) so future
  custom strategies can act on user intent
* Reset an item to ``new`` so a future run re-processes it (operator
  escape hatch — covers "we accidentally consumed today's item, please
  let it run again")

Read access — viewer or higher (everyone in the org can inspect).
Write access — admin only (mutations affect what subsequent workflow
runs do, so we keep them privileged).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import current_user, get_audit_log_service, get_source_items_service
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId, SourceId
from app.services.audit_log import AuditLogService

router = APIRouter()


def _require(svc: Any) -> Any:
    if svc is None:
        # Should never happen now that memory backend has its own impl,
        # but keep the guard so the API always returns a clean message
        # rather than a 500.
        raise HTTPException(
            status_code=503,
            detail="Source-items service not available on this deployment.",
        )
    return svc


# ── reads ───────────────────────────────────────────────────────────────
@router.get("/sources/{source_id}/items")
async def list_items(
    source_id: UUID,
    limit: int = Query(200, ge=1, le=2000),
    user: Principal = Depends(current_user),
    svc: Any = Depends(get_source_items_service),
) -> dict:
    """List items the system has seen for this source, newest-first.
    Includes consumed status, consumed_by_post_id, tags, and timestamps.
    Used by the source detail page's consumption-history view."""
    s = _require(svc)
    rows = await s.list_for_source(
        OrgId(UUID(user.org_id)),
        SourceId(source_id),
        limit=int(limit),
    )
    # Status counts — useful for the header summary on the source page.
    counts = {"new": 0, "consumed": 0, "skipped": 0, "expired": 0}
    for r in rows:
        st = r.get("status") or "new"
        if st in counts:
            counts[st] += 1
    return {"items": rows, "counts": counts, "total": len(rows)}


# ── writes ──────────────────────────────────────────────────────────────
class TagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=20)


@router.patch("/source-items/{item_id}/tags")
async def set_tags(
    item_id: UUID,
    body: TagsBody,
    user: Principal = Depends(current_user),
    svc: Any = Depends(get_source_items_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    """Apply / replace user-applied tags on an item. No de-dup logic looks
    at these yet, but custom strategies can — see the help docs for
    'How items become posts'."""
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    s = _require(svc)
    # Lowercase + trim + dedup so 'Ready' and 'ready' don't both stick.
    cleaned = sorted({t.strip().lower() for t in (body.tags or []) if t.strip()})
    await s.set_tags(OrgId(UUID(user.org_id)), str(item_id), cleaned)
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="source_item.set_tags",
            resource_type="source_item",
            resource_id=item_id,
            after={"tags": cleaned},
        )
    return {"id": str(item_id), "tags": cleaned}


@router.post("/source-items/{item_id}/reset", status_code=200)
async def reset_item(
    item_id: UUID,
    user: Principal = Depends(current_user),
    svc: Any = Depends(get_source_items_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    """Operator escape hatch — flip an item's status back to 'new' so a
    future run can re-process it. Useful when:

      * a workflow misfired and you want to retry the same item
      * a CMS row had a typo when it was first consumed and the upstream
        author has since fixed it
      * QA wants to re-test on a known-good item

    Does NOT delete or rewrite the original Post that may have been
    produced — the audit chain stays intact. Just resets the consumption
    status so the selection layer treats it as new again."""
    if not user.role.can_admin():
        raise HTTPException(status_code=403, detail="Admin role required")
    s = _require(svc)
    await s.reset_to_new(OrgId(UUID(user.org_id)), str(item_id))
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="source_item.reset",
            resource_type="source_item",
            resource_id=item_id,
        )
    return {"id": str(item_id), "status": "new"}
