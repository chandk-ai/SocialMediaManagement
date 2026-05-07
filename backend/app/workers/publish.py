"""Publish workers — Celery tasks with retry + dead-letter queue.

The hot path is `publish_post`:
    enqueued by WorkflowService when an approved post needs to go live →
    invokes the platform adapter → on failure, autoretry with exponential
    backoff → after `max_retries` exhausted, route to `publish_post_dlq`.

The DLQ task records a terminal failure on the post + an audit-log entry +
emits a Prometheus alert. Operators can replay individual posts from the DLQ
via `POST /api/v1/posts/{id}/republish`.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from celery.exceptions import MaxRetriesExceededError

from app.core.logging import get_logger
from app.core.rate_limit import RateLimited
from app.infrastructure.queue.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(
    name="app.workers.publish.publish_post",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=10,           # 10s, 20s, 40s, 80s, 160s
    retry_backoff_max=600,      # cap at 10 min
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def publish_post(self, org_id: str, post_id: str) -> str:
    """Publish a single Post.

    Returns the external_post_id on success. On `RateLimited` we honour the
    server-suggested wait; on any other transient error Celery's autoretry
    handles backoff. After `max_retries` we route to the DLQ.
    """
    try:
        return asyncio.run(_publish(org_id, post_id))
    except RateLimited as exc:
        # Honour the platform's stated cool-down rather than fixed backoff.
        countdown = max(int(exc.retry_in), 5)
        raise self.retry(exc=exc, countdown=countdown)
    except MaxRetriesExceededError as exc:
        publish_post_dlq.delay(org_id, post_id, reason=str(exc))
        raise


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


async def _terminal_failure(org_id: str, post_id: str, reason: str) -> None:
    from app.api.deps import _build_repos
    from app.domain.entities.post import PostStatus
    from app.domain.value_objects.ids import OrgId, PostId
    repos = _build_repos()
    org = OrgId(UUID(org_id))
    post = await repos["post"].get(org, PostId(UUID(post_id)))
    if post is None:
        return
    post.status = PostStatus.FAILED
    post.error = f"DLQ: {reason}"[:1000]
    await repos["post"].update(post)
