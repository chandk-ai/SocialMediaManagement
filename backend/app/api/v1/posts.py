from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_user, get_post_service
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId
from app.schemas.posts import PostOut
from app.services.post_service import PostService

router = APIRouter()


@router.get("", response_model=list[PostOut])
async def list_posts(
    status: str | None = Query(None, description="draft|review|approved|scheduled|published|failed"),
    user: Principal = Depends(current_user),
    svc: PostService = Depends(get_post_service),
) -> list[PostOut]:
    items = await svc.list(OrgId(UUID(user.org_id)), status=status)
    return [PostOut(
        id=p.id, workflow_id=p.workflow_id, run_id=p.run_id,
        platform_id=p.platform_id, text=p.text,
        hashtags=[h.value for h in p.hashtags], status=p.status.value,
        scheduled_for=p.scheduled_for, published_at=p.published_at,
        external_post_id=p.external_post_id, error=p.error, created_at=p.created_at,
    ) for p in items]


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
