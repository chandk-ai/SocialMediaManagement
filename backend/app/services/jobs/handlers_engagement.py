"""Engagement-fetch handlers (Pillar 3).

When a post publishes, run.publish enqueues two ``engagement.fetch``
follow-ups (T+1h and T+24h) so the metrics fetcher pulls platform
engagement and writes it to ``smms.post_metrics``. Pillar 3 fills
in the platform-specific fetch logic; this module is the registry
binding that makes the kind known to the worker.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.services.jobs.handlers import HandlerContext, register_handler

log = get_logger(__name__)


@register_handler("engagement.fetch")
async def engagement_fetch(ctx: HandlerContext) -> dict[str, Any]:
    """Pull engagement metrics for a single post and persist them.
    Pillar 3 implements the platform-specific HTTP calls; for now we
    delegate to the engagement service if it has been bound."""
    svc = ctx.services.get("engagement_service")
    if svc is None:
        log.info("engagement_fetch_no_service",
                 post_id=ctx.job.payload.get("post_id"))
        return {"skipped": True, "reason": "no_service"}
    post_id = ctx.job.payload.get("post_id")
    if not post_id:
        return {"skipped": True, "reason": "no_post_id"}
    try:
        snapshot = await svc.fetch_for_post(
            org_id=ctx.job.org_id, post_id=post_id,
        )
        return {"post_id": post_id, "metrics": snapshot}
    except Exception as exc:                                         # noqa: BLE001
        log.warning("engagement_fetch_failed",
                    post_id=post_id, error=str(exc))
        raise


@register_handler("engagement.aggregate")
async def engagement_aggregate(ctx: HandlerContext) -> dict[str, Any]:
    """Periodic aggregation: roll up post_metrics into source/strategy
    attribution snapshots so the analytics dashboard doesn't recompute
    from scratch on every page load."""
    svc = ctx.services.get("engagement_service")
    if svc is None:
        return {"skipped": True}
    out = await svc.aggregate(org_id=ctx.job.org_id)
    return {"aggregated": out}
