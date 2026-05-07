from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import current_user, get_trigger_service
from app.core.security import Principal
from app.domain.entities.trigger import TriggerKind
from app.domain.value_objects.ids import OrgId, TriggerId, WorkflowId
from app.schemas.triggers import TriggerCreate, TriggerOut
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
