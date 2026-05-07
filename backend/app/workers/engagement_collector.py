"""Engagement collector — periodic Celery task that polls every org's
connected accounts for new comments/DMs and routes draft replies through
the Review pipeline.
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.infrastructure.queue.celery_app import celery_app

log = get_logger(__name__)

# Wire into the beat schedule defined in app.workers.scheduler.
celery_app.conf.beat_schedule.setdefault(
    "smms.engagement.poll",
    {
        "task": "app.workers.engagement_collector.poll_engagement",
        "schedule": 15 * 60,                              # every 15 min
        "options": {"queue": "default"},
    },
)


@celery_app.task(name="app.workers.engagement_collector.poll_engagement",
                 ignore_result=True)
def poll_engagement() -> int:
    return asyncio.run(_poll_async())


async def _poll_async() -> int:
    from app.adapters.llm.mock import MockProvider
    from app.api.deps import _build_repos, get_registry
    from app.services.engagement_service import EngagementService
    from app.services.review_service import ReviewService

    repos = _build_repos()
    registry = get_registry()
    svc = EngagementService(
        platform_repo=repos["platform"], registry=registry,
        llm=MockProvider(),                                # swap with workflow LLM
    )
    # Review service kept available for follow-on persistence; not consumed
    # in this pass (drafts persist directly via repos["review"].add).
    _ = ReviewService(repos["review"], registry)
    yielded = 0
    orgs = list(getattr(repos["platform"], "_s", {}).keys())
    for org_id in orgs:
        async for result in svc.poll_org(org_id):
            yielded += 1
            if not (result.should_reply and result.draft_reply):
                continue
            # Open a review session for the proposed reply — the same
            # WhatsApp / Telegram / in-app pipeline gates approval.
            from app.domain.entities.review_session import ReviewSession
            review = ReviewSession.create(
                org_id=org_id,
                workflow_id=org_id,                        # placeholder — engagement isn't workflow-scoped
                run_id=org_id,                             # placeholder
                channel="in_app",
                recipient="",
                drafts_snapshot=[{
                    "platform_name": result.item.plugin_name,
                    "text": result.draft_reply,
                    "hashtags": [],
                    "in_reply_to": result.item.external_id,
                    "sentiment": result.sentiment.value,
                    "priority": result.priority,
                }],
            )
            await repos["review"].add(review)
    log.info("engagement_polled", items=yielded)
    return yielded
