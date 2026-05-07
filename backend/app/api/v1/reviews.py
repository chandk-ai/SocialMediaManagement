from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.adapters.review_channels.base import DecisionKind
from app.api.deps import (
    current_user,
    get_review_service,
    get_workflow_service,
)
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId, ReviewId, RunId
from app.schemas.reviews import DecisionIn, ReviewOut
from app.services.review_service import ReviewService
from app.services.workflow_service import WorkflowService

router = APIRouter()


def _to_out(r) -> ReviewOut:
    return ReviewOut(
        id=r.id, run_id=r.run_id, workflow_id=r.workflow_id,
        channel=r.channel, recipient=r.recipient, status=r.status.value,
        drafts_snapshot=list(r.drafts_snapshot),
        feedback=r.feedback, decision_at=r.decision_at,
        expires_at=r.expires_at, created_at=r.created_at,
    )


@router.get("", response_model=list[ReviewOut])
async def list_reviews(
    user: Principal = Depends(current_user),
    svc: ReviewService = Depends(get_review_service),
) -> list[ReviewOut]:
    items = await svc.list_open(OrgId(UUID(user.org_id)))
    return [_to_out(r) for r in items]


@router.post("/{review_id}/decision", response_model=ReviewOut)
async def decide(
    review_id: UUID,
    body: DecisionIn,
    user: Principal = Depends(current_user),
    review_svc: ReviewService = Depends(get_review_service),
    wf_svc: WorkflowService = Depends(get_workflow_service),
) -> ReviewOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    try:
        kind = DecisionKind(body.kind.lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid decision kind") from exc
    org_id = OrgId(UUID(user.org_id))
    review = await review_svc.apply_decision_via_api(
        org_id=org_id, review_id=ReviewId(review_id),
        kind=kind, feedback=body.feedback,
    )
    # Resume the workflow run based on the decision.
    await wf_svc.resume_after_review(org_id=org_id, run_id=RunId(review.run_id), review=review)
    return _to_out(review)
