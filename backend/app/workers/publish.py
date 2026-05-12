"""Publish workers — Celery tasks with retry + dead-letter queue.

The hot path is `publish_post`:
    enqueued by /publish_now or WorkflowService when an approved post needs
    to go live → invokes the platform adapter → on **transient** failure,
    autoretry with exponential backoff → after `max_retries` exhausted,
    route to `publish_post_dlq`.

On **permanent** failure (e.g. ``PlatformValidationError`` "Instagram
requires media", auth-rejected token, missing IG Business account) the
worker marks the post FAILED immediately and writes a
``post.publish.failed`` audit event — retrying wouldn't help and the
user wants the feedback now, not 5 minutes later.

Every outcome — success, retry, terminal failure — writes an audit
event so the audit log tells the full story. Previously only
``post.publish {queued: true}`` was recorded at enqueue time and the
worker emitted nothing on its way out; this caused the May 11 2026
"queued and then silence" experience where users saw the queue
acknowledgment but had no signal whether the post actually went out.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from celery.exceptions import MaxRetriesExceededError

from app.adapters.platforms.base import (
    PlatformNotImplemented,
    PlatformValidationError,
)
from app.core.logging import get_logger
from app.core.rate_limit import RateLimited
from app.infrastructure.queue.celery_app import celery_app

log = get_logger(__name__)


# Errors that the platform will **never** accept on retry — payload
# shape problem, missing media, bad credentials. Retrying is pointless
# and just delays the user's "why didn't this work" feedback.
_PERMANENT_ERRORS = (
    PlatformValidationError,
    PlatformNotImplemented,
)


class _PermanentPublishError(Exception):
    """Wraps a permanent failure so we can short-circuit Celery's
    autoretry-on-Exception behaviour."""


@celery_app.task(
    name="app.workers.publish.publish_post",
    bind=True,
    # NOTE: we explicitly DON'T autoretry on PermanentPublishError so
    # validation failures get DLQ'd on the first attempt instead of 5
    # cycles of pointless retries.
    autoretry_for=(Exception,),
    dont_autoretry_for=(_PermanentPublishError,),
    retry_backoff=10,           # 10s, 20s, 40s, 80s, 160s
    retry_backoff_max=600,      # cap at 10 min
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def publish_post(self, org_id: str, post_id: str) -> str:
    """Publish a single Post.

    Returns the external_post_id on success. On `RateLimited` we honour
    the server-suggested wait; on permanent errors we DLQ immediately;
    on any other transient error Celery's autoretry handles backoff.
    """
    try:
        result = asyncio.run(_publish(org_id, post_id))
    except _PERMANENT_ERRORS as exc:
        # Hand straight to the DLQ — no retries. The DLQ task writes the
        # audit event + flips post.status=FAILED so the user sees it
        # within seconds rather than ~5 minutes.
        reason = f"{type(exc).__name__}: {exc}"
        log.warning("publish_permanent_failure",
                    org_id=org_id, post_id=post_id, reason=reason)
        publish_post_dlq.delay(org_id, post_id, reason=reason)
        # Wrap so Celery doesn't treat this as a transient Exception and
        # retry it via autoretry_for=(Exception,).
        raise _PermanentPublishError(reason) from exc
    except RateLimited as exc:
        # Honour the platform's stated cool-down rather than fixed backoff.
        countdown = max(int(exc.retry_in), 5)
        raise self.retry(exc=exc, countdown=countdown)
    except MaxRetriesExceededError as exc:
        publish_post_dlq.delay(org_id, post_id, reason=str(exc))
        raise

    # Success path — write the "post.published" audit event so the
    # audit log shows the full lifecycle (publish → published).
    asyncio.run(_record_success(org_id, post_id, result))
    return result


@celery_app.task(name="app.workers.publish.publish_post_dlq",
                 ignore_result=True)
def publish_post_dlq(org_id: str, post_id: str, reason: str = "") -> None:
    """Terminal failure handler.

    Marks the post as failed (if not already), writes an audit log entry,
    and increments the failure counter. The post stays in this state until
    an operator inspects it via the dashboard's DLQ view.
    """
    log.error("publish_dlq", org_id=org_id, post_id=post_id, reason=reason)
    asyncio.run(_terminal_failure(org_id, post_id, reason))


# ── helpers ────────────────────────────────────────────────────────────────
async def _publish(org_id: str, post_id: str) -> str:
    from app.api.deps import _build_repos, get_registry
    from app.domain.value_objects.content import DraftPost, Hashtag, MediaAsset, MediaKind
    from app.domain.value_objects.ids import OrgId, PostId
    from app.services.workflow_service import WorkflowService

    repos = _build_repos()
    registry = get_registry()
    svc = WorkflowService(
        repo=repos["workflow"], run_repo=repos["run"],
        source_repo=repos["source"], platform_repo=repos["platform"],
        post_repo=repos["post"], registry=registry,
        review_repo=repos["review"],
    )
    org = OrgId(UUID(org_id))
    post = await repos["post"].get(org, PostId(UUID(post_id)))
    if post is None:
        raise RuntimeError(f"post {post_id} not found")
    target = await repos["platform"].get(org, post.platform_id)
    if target is None:
        raise RuntimeError(f"platform {post.platform_id} not found")
    media = [
        MediaAsset(url=m.get("url",""), kind=MediaKind(m.get("kind","image")),
                   alt_text=m.get("alt_text"))
        for m in (post.media or [])
    ] if not isinstance(post.media[0] if post.media else None, MediaAsset) else post.media
    draft = DraftPost(
        platform_name=target.plugin_name, text=post.text,
        hashtags=[h if isinstance(h, Hashtag) else Hashtag(h) for h in post.hashtags],
        media=media,
    )
    await svc._publish(post, draft, target)
    return post.external_post_id or ""


async def _record_success(org_id: str, post_id: str, external_id: str) -> None:
    """Audit-log a successful publish so the user sees the full
    lifecycle (queued → published) in /audit."""
    from app.api.deps import get_audit_log_service
    audit = get_audit_log_service()
    if audit is None:
        return
    try:
        await audit.record(
            org_id=org_id, actor_type="system",
            action="post.published",
            resource_type="post", resource_id=post_id,
            after={"external_post_id": external_id or None},
        )
    except Exception as exc:                                          # noqa: BLE001
        log.warning("audit_publish_success_failed",
                    post_id=post_id, error=str(exc))


async def _terminal_failure(org_id: str, post_id: str, reason: str) -> None:
    from app.api.deps import _build_repos, get_audit_log_service
    from app.domain.entities.post import PostStatus
    from app.domain.value_objects.ids import OrgId, PostId
    repos = _build_repos()
    org = OrgId(UUID(org_id))
    post = await repos["post"].get(org, PostId(UUID(post_id)))
    if post is None:
        return
    post.status = PostStatus.FAILED
    post.error = reason[:1000]
    await repos["post"].update(post)
    # Make the DLQ visible in the org's audit log — operators looking at
    # /audit see *every* terminal failure, not just user-initiated ones.
    # Use action="post.publish.failed" so a single search filter catches
    # both permanent rejections and exhausted-retry failures.
    audit = get_audit_log_service()
    if audit is not None:
        await audit.record(
            org_id=org_id,
            actor_type="system",
            action="post.publish.failed",
            resource_type="post",
            resource_id=post_id,
            after={
                "platform_id": str(post.platform_id),
                "reason": reason[:500],
            },
        )
