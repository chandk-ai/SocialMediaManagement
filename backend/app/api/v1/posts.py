from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel

from app.api.deps import current_user, get_audit_log_service, get_post_service
from app.core.security import Principal
from app.domain.entities.post import PostStatus
from app.domain.value_objects.content import Hashtag, MediaAsset, MediaKind
from app.domain.value_objects.ids import OrgId, PostId
from app.schemas.posts import PostOut
from app.services.audit_log import AuditLogService
from app.services.post_service import PostService

router = APIRouter()


def _to_out(p, platform=None) -> PostOut:
    """Serialize a Post into the API DTO.

    ``platform`` is the resolved Platform row for ``p.platform_id`` — pass
    it when you've already fetched it (the list endpoint batches lookups
    to avoid N+1). When unset, the target-display fields are returned as
    None and the UI falls back to the platform_id UUID.
    """
    # ``p.media`` is a list of MediaAsset dataclasses (or already-dict
    # shapes when the supabase repo hasn't fully rehydrated). Handle
    # both gracefully so list_posts doesn't 500 on legacy rows.
    media_out = []
    for m in (p.media or []):
        if hasattr(m, "url"):
            media_out.append({
                "url": m.url,
                "kind": getattr(m.kind, "value", m.kind) if m.kind else "image",
                "alt_text": getattr(m, "alt_text", None),
            })
        elif isinstance(m, dict):
            media_out.append({
                "url": m.get("url", ""),
                "kind": m.get("kind", "image"),
                "alt_text": m.get("alt_text"),
            })
    return PostOut(
        id=p.id, workflow_id=p.workflow_id, run_id=p.run_id,
        platform_id=p.platform_id,
        platform_plugin_name=getattr(platform, "plugin_name", None) if platform else None,
        platform_display_name=getattr(platform, "display_name", None) if platform else None,
        account_handle=getattr(platform, "account_handle", None) if platform else None,
        text=p.text,
        hashtags=[h.value for h in p.hashtags], status=p.status.value,
        scheduled_for=p.scheduled_for, published_at=p.published_at,
        external_post_id=p.external_post_id, error=p.error, created_at=p.created_at,
        media=media_out,
    )


async def _batch_platforms_for_posts(
    org_id: OrgId, posts: list,
) -> dict:
    """Fetch every unique Platform referenced by ``posts`` in one pass and
    return ``{platform_id: Platform}``. Avoids the N+1 that would happen
    if ``_to_out`` resolved per-post.

    The platform repo doesn't expose a bulk fetch method, so we still do
    one DB call per unique platform — but the de-dup means a 50-post run
    with 3 distinct platforms costs 3 lookups, not 50."""
    from app.api.deps import _build_repos
    platform_repo = _build_repos()["platform"]
    seen: dict = {}
    for p in posts:
        if p.platform_id in seen:
            continue
        try:
            seen[p.platform_id] = await platform_repo.get(org_id, p.platform_id)
        except Exception:                                            # noqa: BLE001
            seen[p.platform_id] = None
    return seen


@router.get("", response_model=list[PostOut])
async def list_posts(
    status: str | None = Query(None, description="draft|review|approved|scheduled|published|failed"),
    run_id: UUID | None = Query(None, description="Filter to posts from a single workflow run — used for sibling display"),
    user: Principal = Depends(current_user),
    svc: PostService = Depends(get_post_service),
) -> list[PostOut]:
    items = await svc.list(OrgId(UUID(user.org_id)), status=status)
    if run_id is not None:
        # Post-filter by run_id at the API layer so neither the service
        # nor the repo contract needs a new parameter. Posts pages are
        # already paginated client-side, so volume is not a concern here.
        items = [p for p in items if str(p.run_id) == str(run_id)]
    plat_by_id = await _batch_platforms_for_posts(OrgId(UUID(user.org_id)), items)
    return [_to_out(p, plat_by_id.get(p.platform_id)) for p in items]


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


class MediaIn(BaseModel):
    """One media attachment. ``url`` must be a publicly reachable HTTPS
    URL because some platforms (Instagram, Pinterest) fetch the binary
    server-side rather than accepting a multipart upload from us. For
    private storage (Google Drive folders shared "with link", Supabase
    Storage public buckets, S3 with a presigned URL), use a presigned
    or public-read URL."""
    url: str
    kind: str = "image"          # "image" | "video" | "gif"
    alt_text: str | None = None


class PostUpdateBody(BaseModel):
    text: str | None = None
    hashtags: list[str] | None = None
    scheduled_for: datetime | None = None
    # Pass ``[]`` to clear; ``None`` (default) leaves existing media
    # untouched.
    media: list[MediaIn] | None = None


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
    if body.media is not None:
        # Normalize MediaKind via the enum so an invalid value short-
        # circuits with a 422 instead of silently coercing later.
        try:
            p.media = [
                MediaAsset(
                    url=m.url,
                    kind=MediaKind(m.kind),
                    alt_text=m.alt_text,
                )
                for m in body.media
            ]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await svc.repo.update(p)
    # Resolve the platform so the response includes target chip data —
    # the UI re-renders the post card with this response and would
    # otherwise lose the chip until the next list-refresh.
    from app.api.deps import _build_repos
    plat = await _build_repos()["platform"].get(OrgId(UUID(user.org_id)), p.platform_id)
    return _to_out(p, plat)


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
    from app.api.deps import _build_repos
    plat = await _build_repos()["platform"].get(OrgId(UUID(user.org_id)), p.platform_id)
    return _to_out(p, plat)


@router.post("/{post_id}/publish_now")
async def publish_post_now(
    post_id: UUID,
    user: Principal = Depends(current_user),
    svc: PostService = Depends(get_post_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    """Approve (if needed) and immediately enqueue for publishing.

    Pre-flight checks here so the user sees the real problem (no media,
    platform not connected, missing OAuth credential) **before** the
    publish job vanishes into Celery's autoretry loop. Previously a
    silently broken platform led to a queued-but-never-completed state
    that produced no audit signal for ~10 minutes.
    """
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    p = await _require_owned(svc, OrgId(UUID(user.org_id)), PostId(post_id))
    if p.status not in {PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.REVIEW, PostStatus.DRAFT}:
        raise HTTPException(
            status_code=409,
            detail=f"post is {p.status.value}; cannot publish",
        )

    # ── Pre-flight: platform exists + has stored OAuth credentials ──
    # We use the same repo factory the workers use. Platform fetches
    # are org-scoped.
    from app.api.deps import _build_repos
    platform_repo = _build_repos()["platform"]
    platform = await platform_repo.get(OrgId(UUID(user.org_id)), p.platform_id)
    if platform is None:
        raise HTTPException(
            status_code=422,
            detail=f"Platform {p.platform_id} not found. The platform "
                   "may have been deleted — reconnect it from Settings → "
                   "Platforms.",
        )
    if getattr(platform, "credentials", None) is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Platform '{platform.display_name}' ({platform.plugin_name}) "
                f"shows status='{platform.status.value if hasattr(platform.status, 'value') else platform.status}' but no OAuth token is stored. "
                f"The connect flow likely didn't complete. "
                f"Go to Settings → Platforms → Reconnect this account."
            ),
        )

    p.approve()
    p.scheduled_for = None
    await svc.repo.update(p)
    try:
        from app.workers.publish import publish_post as publish_task
        publish_task.delay(user.org_id, str(post_id))
    except Exception as exc:                                # noqa: BLE001
        if audit is not None:
            await audit.record(
                org_id=user.org_id,
                action="post.publish.fail",
                resource_type="post",
                resource_id=post_id,
                after={"reason": str(exc)},
            )
        return {"queued": False, "reason": str(exc), "post_id": str(post_id)}
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="post.publish",
            resource_type="post",
            resource_id=post_id,
            after={"platform_id": str(p.platform_id), "queued": True},
        )
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
