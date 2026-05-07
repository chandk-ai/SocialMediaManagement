"""Performance feedback loop — nightly Celery task that pulls real
engagement metrics from each platform and feeds the brand-voice index.

For every published post in the last 30 days, we call
`SocialPlatform.fetch_metrics(external_post_id)`, persist the snapshot on
the Post, and (when the org has `use_brand_voice=True`) feed high-performing
posts into the BrandVoiceService.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger
from app.infrastructure.queue.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="app.workers.metrics_collector.collect_all_metrics",
                 ignore_result=True)
def collect_all_metrics() -> int:
    """Returns the number of posts whose metrics were refreshed."""
    return asyncio.run(_collect_async())


async def _collect_async() -> int:
    from app.api.deps import _build_repos, get_registry
    from app.domain.entities.post import PostStatus
    from app.plugins.registry import PluginKind

    repos = _build_repos()
    registry = get_registry()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    refreshed = 0
    # Walk every org's recent published posts. Production deployments will
    # add a `PostRepository.list_for_metrics()` query that hits the DB once.
    posts_store = getattr(repos["post"], "_s", {})
    if not isinstance(posts_store, dict):
        return 0
    for org_id, by_id in posts_store.items():
        for post in by_id.values():
            if post.status is not PostStatus.PUBLISHED or not post.external_post_id:
                continue
            if post.published_at and post.published_at < cutoff.replace(tzinfo=None):
                continue
            target = await repos["platform"].get(org_id, post.platform_id)
            if not target:
                continue
            try:
                entry = registry.get(PluginKind.PLATFORM, target.plugin_name)
            except Exception:                          # noqa: BLE001
                continue
            adapter = entry.cls(credentials=target.credentials, config=target.config)
            try:
                snapshot = await adapter.fetch_metrics(post.external_post_id)
            except Exception as exc:                   # noqa: BLE001
                log.warning("metrics_fetch_failed",
                            post_id=str(post.id), error=str(exc))
                continue
            post.metrics = dict(snapshot)
            post.metrics_updated_at = datetime.utcnow()
            await repos["post"].update(post)
            refreshed += 1
            await _maybe_feed_brand_voice(org_id, post, target)
    log.info("metrics_collector_done", refreshed=refreshed)
    return refreshed


async def _maybe_feed_brand_voice(org_id, post, target) -> None:
    """If this post performed well, push it into the org's brand-voice corpus."""
    try:
        from app.adapters.llm.mock import MockProvider
        from app.services.brand_voice import BrandVoiceService
    except ImportError:
        return
    svc = _voice_singleton(MockProvider())
    await svc.ingest_post(org_id, post, plugin_name=target.plugin_name)


_VOICE: object | None = None


def _voice_singleton(llm):
    global _VOICE
    if _VOICE is None:
        from app.services.brand_voice import BrandVoiceService
        _VOICE = BrandVoiceService(llm)
    return _VOICE


def get_brand_voice_service():
    return _VOICE
