from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import current_user, get_audit_log_service, get_trigger_service
from app.core.security import Principal
from app.domain.entities.trigger import TriggerKind
from app.domain.value_objects.ids import OrgId, TriggerId, WorkflowId
from app.schemas.triggers import TriggerCreate, TriggerOut, TriggerUpdate
from app.services.audit_log import AuditLogService
from app.services.trigger_service import TriggerService

router = APIRouter()


def _to_out(t) -> TriggerOut:
    return TriggerOut(
        id=t.id, workflow_id=t.workflow_id, plugin_name=t.plugin_name,
        display_name=t.display_name, kind=t.kind.value, is_active=t.is_active,
        config=t.config, allowed_senders=list(t.allowed_senders),
        review_channel=t.review_channel, review_recipient=t.review_recipient,
        created_at=t.created_at, last_fired_at=t.last_fired_at,
    )


@router.get("", response_model=list[TriggerOut])
async def list_triggers(
    user: Principal = Depends(current_user),
    svc: TriggerService = Depends(get_trigger_service),
) -> list[TriggerOut]:
    items = await svc.list(OrgId(UUID(user.org_id)))
    return [_to_out(t) for t in items]


@router.post("", response_model=TriggerOut, status_code=status.HTTP_201_CREATED)
async def create_trigger(
    body: TriggerCreate,
    user: Principal = Depends(current_user),
    svc: TriggerService = Depends(get_trigger_service),
) -> TriggerOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    # Map plugin_name to TriggerKind
    try:
        kind = TriggerKind(body.plugin_name) if body.plugin_name in TriggerKind._value2member_map_ \
               else TriggerKind.WEBHOOK
    except ValueError:
        kind = TriggerKind.WEBHOOK
    t = await svc.create(
        org_id=OrgId(UUID(user.org_id)),
        workflow_id=WorkflowId(body.workflow_id),
        plugin_name=body.plugin_name,
        display_name=body.display_name,
        kind=kind, config=body.config,
        allowed_senders=body.allowed_senders,
        review_channel=body.review_channel,
        review_recipient=body.review_recipient,
    )
    return _to_out(t)


@router.patch("/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    trigger_id: UUID,
    body: TriggerUpdate,
    user: Principal = Depends(current_user),
    svc: TriggerService = Depends(get_trigger_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> TriggerOut:
    """Edit display_name, config, allowed_senders, review_channel,
    review_recipient, or is_active. See ``TriggerUpdate`` docstring
    for why plugin_name / workflow_id are excluded."""
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    org_id = OrgId(UUID(user.org_id))
    # Snapshot the before-state for the audit log — we only record the
    # fields that actually changed so the trail isn't noisy. Skipping
    # config from the diff because it can be large + secrets-bearing.
    before = await svc.get(org_id, TriggerId(trigger_id))
    if before is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    try:
        updated = await svc.update(
            org_id=org_id, trigger_id=TriggerId(trigger_id),
            display_name=body.display_name,
            config=body.config,
            allowed_senders=body.allowed_senders,
            review_channel=body.review_channel,
            review_recipient=body.review_recipient,
            is_active=body.is_active,
        )
    except KeyError as exc:
        # Service raises KeyError when an unknown plugin name was
        # passed for review_channel (registry lookup miss).
        raise HTTPException(
            status_code=422,
            detail=f"unknown review_channel plugin: {exc}",
        ) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    if audit is not None:
        # Only show the diff of changed scalar fields — config goes
        # under a boolean flag because it may contain secrets.
        diff: dict = {}
        if body.display_name is not None and body.display_name != before.display_name:
            diff["display_name"] = {"from": before.display_name, "to": updated.display_name}
        if body.is_active is not None and bool(body.is_active) != before.is_active:
            diff["is_active"] = {"from": before.is_active, "to": updated.is_active}
        if body.allowed_senders is not None:
            diff["allowed_senders_changed"] = (
                list(before.allowed_senders) != list(updated.allowed_senders)
            )
        if body.review_channel is not None:
            diff["review_channel"] = {
                "from": before.review_channel, "to": updated.review_channel,
            }
        if body.review_recipient is not None:
            diff["review_recipient"] = {
                "from": before.review_recipient, "to": updated.review_recipient,
            }
        if body.config is not None:
            diff["config_replaced"] = True
        await audit.record(
            org_id=user.org_id, action="trigger.update",
            resource_type="trigger", resource_id=trigger_id,
            after=diff,
        )
    return _to_out(updated)


@router.delete("/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trigger(
    trigger_id: UUID,
    user: Principal = Depends(current_user),
    svc: TriggerService = Depends(get_trigger_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> None:
    """Hard-delete a trigger. The trigger row is gone — there's no
    soft-delete column today, so deleting a webhook means its URL stops
    working immediately. Use PATCH ``is_active=false`` for reversible
    pause."""
    if not user.role.can_admin():
        raise HTTPException(status_code=403, detail="Admin role required")
    org_id = OrgId(UUID(user.org_id))
    # Fetch first so we can audit-log meaningful context (which plugin
    # / which workflow) and return 404 cleanly.
    t = await svc.get(org_id, TriggerId(trigger_id))
    if t is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    await svc.delete(org_id, TriggerId(trigger_id))
    if audit is not None:
        await audit.record(
            org_id=user.org_id, action="trigger.delete",
            resource_type="trigger", resource_id=trigger_id,
            after={
                "plugin_name": t.plugin_name,
                "display_name": t.display_name,
                "workflow_id": str(t.workflow_id),
            },
        )
