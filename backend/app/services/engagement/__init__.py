"""Engagement feedback loop — Pillar 3.

Pulls platform metrics back into the system, attributes them to the
source / strategy / time-of-day that produced the post, and surfaces
the ranking to the selection layer + adaptive scheduler.

Public surface:

    EngagementService.fetch_for_post(org_id, post_id) -> snapshot dict
    EngagementService.aggregate(org_id) -> rollup_count
    EngagementService.list_recent(org_id, *, limit) -> [snapshot]
    EngagementService.learnings(org_id, *, dim) -> [{key, avg, samples}]

Three implementations of fetcher logic per platform live in
``adapters.py`` — they're thin shims over the platform adapter's
``get_metrics()`` method (added in Pillar 3 to the SocialPlatform
contract).

When the Postgres backend isn't available the service still works
in-memory so dev / tests get the same shape; the rollup is
recomputed each call.
"""
from app.services.engagement.service import EngagementService
from app.services.engagement.attribution import (
    AttributionDimension, AttributionResult,
)

__all__ = [
    "EngagementService", "AttributionDimension", "AttributionResult",
]
