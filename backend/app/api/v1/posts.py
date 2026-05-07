from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel

from app.api.deps import current_user, get_post_service
from app.core.security import Principal
from app.domain.entities.post import PostStatus
from app.domain.value_objects.content import Hashtag
from app.domain.value_objects.ids import OrgId, PostId
from app.schemas.posts import PostOut
from app.services.post_service import PostService

router = APIRouter()


def _to_out(p) -> PostOut:
    return PostOut(
        id=p.id, workflow_id=p.workflow_id, run_id=p.run_id,
        platform_id=p.platform_id, text=p.text,
        hashtags=[h.value for h in p.hashtags], status=p.status.value,
        scheduled_for=p.scheduled_for, published_at=p.published_at,
        external_post_id=p.external_post_id, error=p.error, created_at=p.created_at,
    )


@router.get("", response_model=list[PostOut])
async def list_posts(
    status: str | None = Query(None, description="draft|review|approved|scheduled|published|failed"),
    user: Principal = Depends(current_user),
    svc: PostService = Depends(get_post_service),
) -> list[PostOut]:
    items = await svc.list(OrgId(UUID(user.org_id)), status=status)
    return [_to_out(p) for p in items]


@router.post("/{post_id}/republish")
async def republish(
    post_id: UUID,
    user: Principal = Depends(current_user),
) -> dict:
    """Re-enqueue a previously failed post. Used to drain the DLQ."""
    try:
        from app.workers.publish import publish_post
        publish_post.delay(user.org_id, str(post_id))
    except Exception as exc:                                # noqa: BLE001
        return {"queued": False, "reason": str(exc)}
    return {"queued": True, "post_id": str(post_id)}


class PostUpdateBody(BaseModel):
    text: str | None = None
    hashtags: list[str] | None = None
    scheduled_for: datetime | None = None


async def _require_owned(svc: PostService, org_id: OrgId, post_id: PostId):
    p = await svc.repo.get(org_id, post_id)
    if p is None:
        raise HTTPException(status_code=404, detail="post not found")
    return p


@router.patch("/{post_id}", response_model=PostOut)
async def update_post(
    post_id: UUID,
    body: PostUpdateBody,
    user: Principal = Depends(current_user),
    svc: PostService = Depends(get_post_service),
) -> PostOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    p = await _require_owned(svc, OrgId(UUID(user.org_id)), PostId(post_id))
    if body.text is not None:
        p.text = body.text
    if body.hashtags is not None:
        p.hashtags = [Hashtag(value=h) for h in body.hashtags]
    if body.scheduled_for is not None:
        p.scheduled_for = body.scheduled_for
        if p.status == PostStatus.APPROVED:
            p.status = PostStatus.SCHEDULED
    await svc.repo.update(p)
    return _to_out(p)


@router.post("/{post_id}/approve", response_model=PostOut)
async def approve_post(
    post_id: UUID,
    user: Principal = Depends(current_user),
    svc: PostService = Depends(get_post_service),
) -> PostOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    p = await _require_owned(svc, OrgId(UUID(user.org_id)), PostId(post_id))
    p.approve()
    await svc.repo.update(p)
    return _to_out(p)


@router.post("/{post_id}/publish_now")
async def publish_post_now(
    post_id: UUID,
    user: Principal = Depends(current_user),
    svc: PostService = Depends(get_post_service),
) -> dict:
    """Approve (if needed) and immediately enqueue for publishing."""
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    p = await _require_owned(svc, OrgId(UUID(user.org_id)), PostId(post_id))
    if p.status not in {PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.REVIEW, PostStatus.DRAFT}:
        raise HTTPException(
            status_code=409,
            detail=f"post is {p.status.value}; cannot publish",
        )
    p.approve()
    p.scheduled_for = None
    await svc.repo.update(p)
    try:
        from app.workers.publish import publish_post as publish_task
        publish_task.delay(user.org_id, str(post_id))
    except Exception as exc:                                # noqa: BLE001
        return {"queued": False, "reason": str(exc), "post_id": str(post_id)}
    return {"queued": True, "post_id": str(post_id)}


@router.delete("/{post_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_post(
    post_id: UUID,
    user: Principal = Depends(current_user),
    svc: PostService = Depends(get_post_service),
) -> None:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    await svc.repo.delete(OrgId(UUID(user.org_id)), PostId(post_id))
