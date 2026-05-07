"""Webhook endpoints — external systems (WhatsApp, Instagram, Zapier, ...)
hit these to fire triggers."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from fastapi.responses import PlainTextResponse

from app.adapters.review_channels.base import parse_decision
from app.api.deps import (
    get_review_service,
    get_trigger_service,
    get_workflow_service,
)
from app.core.logging import get_logger
from app.domain.value_objects.ids import RunId, TriggerId
from app.schemas.triggers import WebhookAck
from app.services.review_service import ReviewService
from app.services.trigger_service import TriggerService
from app.services.workflow_service import WorkflowService

router = APIRouter()
log = get_logger(__name__)


# ── handshake (Meta GET) for WhatsApp & Instagram ──────────────────────────
@router.get("/whatsapp/{trigger_id}", response_class=PlainTextResponse)
@router.get("/instagram/{trigger_id}", response_class=PlainTextResponse)
async def meta_handshake(
    request: Request,
    trigger_id: UUID = Path(...),
    svc: TriggerService = Depends(get_trigger_service),
) -> str:
    trigger = await svc.get_any(TriggerId(trigger_id))
    if not trigger:
        raise HTTPException(status_code=404, detail="trigger not found")
    adapter = svc.adapter_for(trigger)
    challenge = getattr(adapter, "verify_handshake", lambda q: None)(dict(request.query_params))
    if challenge is None:
        raise HTTPException(status_code=403, detail="verify_token mismatch")
    return challenge


# ── inbound webhooks ───────────────────────────────────────────────────────
@router.post("/whatsapp/{trigger_id}", response_model=WebhookAck)
async def whatsapp_webhook(
    trigger_id: UUID = Path(...),
    request: Request = None,                          # type: ignore[assignment]
    svc: TriggerService = Depends(get_trigger_service),
    wf_svc: WorkflowService = Depends(get_workflow_service),
    review_svc: ReviewService = Depends(get_review_service),
) -> WebhookAck:
    return await _handle_messaging_webhook(
        trigger_id=trigger_id, channel="whatsapp",
        request=request, svc=svc, wf_svc=wf_svc, review_svc=review_svc,
    )


@router.post("/instagram/{trigger_id}", response_model=WebhookAck)
async def instagram_webhook(
    trigger_id: UUID = Path(...),
    request: Request = None,                          # type: ignore[assignment]
    svc: TriggerService = Depends(get_trigger_service),
    wf_svc: WorkflowService = Depends(get_workflow_service),
    review_svc: ReviewService = Depends(get_review_service),
) -> WebhookAck:
    return await _handle_messaging_webhook(
        trigger_id=trigger_id, channel="instagram",
        request=request, svc=svc, wf_svc=wf_svc, review_svc=review_svc,
    )


@router.post("/telegram/{trigger_id}", response_model=WebhookAck)
async def telegram_webhook(
    trigger_id: UUID = Path(...),
    request: Request = None,                          # type: ignore[assignment]
    svc: TriggerService = Depends(get_trigger_service),
    wf_svc: WorkflowService = Depends(get_workflow_service),
    review_svc: ReviewService = Depends(get_review_service),
) -> WebhookAck:
    return await _handle_messaging_webhook(
        trigger_id=trigger_id, channel="telegram",
        request=request, svc=svc, wf_svc=wf_svc, review_svc=review_svc,
    )


@router.post("/{trigger_id}", response_model=WebhookAck)
async def generic_webhook(
    trigger_id: UUID = Path(...),
    request: Request = None,                          # type: ignore[assignment]
    svc: TriggerService = Depends(get_trigger_service),
    wf_svc: WorkflowService = Depends(get_workflow_service),
) -> WebhookAck:
    trigger = await svc.get_any(TriggerId(trigger_id))
    if not trigger or not trigger.is_active:
        raise HTTPException(status_code=404, detail="trigger not found or inactive")
    body = await request.body()
    payload = json.loads(body or b"{}")
    events = await svc.parse_payload(trigger, payload, dict(request.headers), body)
    started: list[UUID] = []
    for ev in events:
        run = await wf_svc.run_from_trigger(
            trigger=trigger, directive=ev.directive, initiator=ev.sender,
        )
        started.append(run.id)
    return WebhookAck(received=len(events), started=started)


# ── shared logic for messaging triggers ────────────────────────────────────
async def _handle_messaging_webhook(
    *, trigger_id: UUID, channel: str,
    request: Request, svc: TriggerService,
    wf_svc: WorkflowService, review_svc: ReviewService,
) -> WebhookAck:
    trigger = await svc.get_any(TriggerId(trigger_id))
    if not trigger or not trigger.is_active:
        raise HTTPException(status_code=404, detail="trigger not found or inactive")
    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc

    events = await svc.parse_payload(trigger, payload, dict(request.headers), body)

    started: list[UUID] = []
    for ev in events:
        # If this message is a reply to a pending review, route it as a decision.
        review, decision = await review_svc.apply_reply(
            channel=channel, sender=ev.sender,
            reply_text=ev.directive, in_reply_to=ev.in_reply_to,
        )
        if review is not None:
            log.info("trigger_routed_to_review",
                     channel=channel, decision=decision.kind.value,
                     review_id=str(review.id))
            run = await wf_svc.resume_after_review(
                org_id=review.org_id, run_id=RunId(review.run_id), review=review,
            )
            started.append(run.id)
            continue

        # Otherwise it's a fresh request — start a new workflow run.
        run = await wf_svc.run_from_trigger(
            trigger=trigger, directive=ev.directive, initiator=ev.sender,
        )
        started.append(run.id)
    return WebhookAck(received=len(events), started=started)
