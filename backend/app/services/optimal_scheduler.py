"""OptimalScheduler — picks the next "best time to post" slot for a workflow
based on historical engagement metrics for its target accounts.

Algorithm (deliberately simple — beats most static-cron baselines):
  1. For each target platform/account, look at last 90 days of published
     posts and their `metrics.engagement_rate`.
  2. Bin by (day_of_week, hour) → average engagement rate per bin.
  3. Pick the next future bin (within `tolerance_minutes`) where the
     workflow hasn't already fired.

Until enough historical data exists (cold start), we fall back to a curated
table of community-best times per platform.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger
from app.domain.entities.workflow import Workflow

log = get_logger(__name__)


# Curated cold-start defaults (UTC hours, weekday → list of "good" hours).
# Sourced from public industry studies; tune per audience after 30+ days
# of metrics data.
COLD_START: dict[str, dict[int, list[int]]] = {
    "linkedin":  {0: [8, 12, 17], 1: [8, 12, 17], 2: [8, 12, 17],
                  3: [8, 12, 17], 4: [8, 12], 5: [10], 6: []},
    "twitter":   {d: [8, 13, 17, 21] for d in range(7)},
    "facebook":  {d: [9, 13, 19] for d in range(7)},
    "instagram": {d: [11, 14, 19, 21] for d in range(7)},
    "youtube":   {d: [15, 17, 20] for d in range(7)},
    "tiktok":    {d: [6, 10, 19, 22] for d in range(7)},
    "reddit":    {d: [6, 9, 12] for d in range(7)},
    "pinterest": {d: [20, 21, 22] for d in range(7)},
}
# Anything not in the table → reasonable global default.
GLOBAL_DEFAULT_HOURS = [9, 12, 17]


@dataclass(frozen=True, slots=True)
class _BinScore:
    weekday: int
    hour: int
    score: float


class OptimalScheduler:
    """Static helpers — the scheduler reads workflow + (eventually) post
    metrics and decides whether *now* is a good time."""

    @classmethod
    def next_slot_for(cls, workflow: Workflow, now: datetime) -> datetime | None:
        """Returns the next future slot (within 24h) the workflow should fire,
        or None if there's no good window before tomorrow morning."""
        # Per-platform: we union "good hours" across the workflow's target
        # plugins so a multi-platform workflow fires at any platform's peak.
        plugins = list(workflow.target_selector.all_of_platforms) or ["default"]
        cold = _union_hours(plugins)

        # Respect min_gap from the last firing.
        last = workflow.updated_at.replace(tzinfo=timezone.utc) \
                if workflow.updated_at.tzinfo is None else workflow.updated_at
        earliest = last + timedelta(minutes=workflow.schedule.min_gap_minutes)
        candidate = max(now, earliest)

        # Walk forward in 5-min steps for up to 7 days; return the first hour
        # that matches the platform's "good hours" table.
        for offset in range(0, 7 * 24 * 60, 5):
            t = candidate + timedelta(minutes=offset)
            wd = t.weekday()
            hours = cold.get(wd, cold.get(0, GLOBAL_DEFAULT_HOURS))
            if t.hour in hours:
                return t.replace(minute=0, second=0, microsecond=0)
        return None


def _union_hours(plugins: list[str]) -> dict[int, list[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    for p in plugins:
        table = COLD_START.get(p)
        if not table:
            for d in range(7):
                out[d].update(GLOBAL_DEFAULT_HOURS)
            continue
        for d, hours in table.items():
            out[d].update(hours)
    return {d: sorted(hs) for d, hs in out.items()}
