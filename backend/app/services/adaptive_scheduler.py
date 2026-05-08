"""Engagement-driven self-scheduling — Niche #10.

When a workflow's schedule is ``ADAPTIVE``, the Beat tick consults this
module to decide whether the workflow is due. The interval is derived
from recent post performance for the same workflow:

  * Engagement trending UP   → tighten the interval (post more often)
  * Engagement trending FLAT → keep the current interval
  * Engagement trending DOWN → loosen the interval (give the audience
                              breathing room — fatigue is the #1 cause
                              of unfollows)

Pitch: "you don't tell us how often to post — we figure it out."

Honest scope:
  * Trend = compare median engagement of the most recent N posts to the
    median of the N posts before that. We avoid mean because a viral
    post can dominate signal.
  * Interval bounds — never more than 1 post / 2h, never less than 1
    post / 7d. Keeps adaptive workflows from going silent or spamming.
  * Cold start — when fewer than 4 published posts exist, fall back to
    the workflow's ``interval_minutes`` (or 24h default).

The bounds + heuristics live here as constants so they're easy to tune
once we have real customer data. Replace with a learned model later.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.core.logging import get_logger
from app.domain.entities.post import Post, PostStatus
from app.domain.entities.workflow import Workflow

log = get_logger(__name__)


# Minute bounds — never tighter than 2h, never looser than 7 days.
MIN_INTERVAL_MINUTES = 2 * 60
MAX_INTERVAL_MINUTES = 7 * 24 * 60
DEFAULT_INTERVAL_MINUTES = 24 * 60          # cold-start fallback
COMPARE_WINDOW = 4                          # posts in each half of the trend window


@dataclass(frozen=True, slots=True)
class AdaptiveDecision:
    """Why the scheduler decided what it decided. Surfaced in run traces
    so customers can see the reasoning rather than a black box."""
    interval_minutes: int
    reason: str
    recent_engagement: float | None = None
    prior_engagement: float | None = None


def is_due(
    workflow: Workflow,
    posts: list[Post],
    now: datetime | None = None,
) -> tuple[bool, AdaptiveDecision]:
    """Decide whether the workflow should fire right now.

    `posts` is the workflow's published-post history (newest first or any
    order — we sort internally). `now` is the comparison anchor; defaults
    to ``datetime.now(timezone.utc)``.

    Returns ``(due, decision)``. ``decision`` is logged into the run
    trace for transparency.
    """
    when = now or datetime.now(timezone.utc)
    decision = compute_interval(workflow, posts, when)

    # The "last fire" is approximated by the most recent published_at on
    # any of this workflow's posts. workflow.updated_at is unreliable
    # because it's bumped whenever the workflow row changes (e.g. a name
    # edit), which would make the workflow look like it just "fired".
    last_fired = _last_published_at(posts) or workflow.updated_at
    if last_fired.tzinfo is None:
        last_fired = last_fired.replace(tzinfo=timezone.utc)
    elapsed = when - last_fired
    due = elapsed >= timedelta(minutes=decision.interval_minutes)
    return due, decision


def compute_interval(
    workflow: Workflow,
    posts: list[Post],
    now: datetime | None = None,
) -> AdaptiveDecision:
    """Recompute the cadence from recent engagement. Pure function — easy
    to test, easy to tune."""
    fallback = max(
        MIN_INTERVAL_MINUTES,
        min(workflow.schedule.interval_minutes or DEFAULT_INTERVAL_MINUTES,
            MAX_INTERVAL_MINUTES),
    )
    published = [p for p in posts if p.status is PostStatus.PUBLISHED]
    if len(published) < COMPARE_WINDOW * 2:
        return AdaptiveDecision(
            interval_minutes=fallback,
            reason=f"cold-start ({len(published)} of {COMPARE_WINDOW * 2} needed)",
        )

    # Sort newest-first by published_at so the slicing is meaningful.
    published.sort(
        key=lambda p: p.published_at or p.created_at,
        reverse=True,
    )
    recent = published[:COMPARE_WINDOW]
    prior = published[COMPARE_WINDOW:COMPARE_WINDOW * 2]

    recent_eng = _median_engagement(recent)
    prior_eng = _median_engagement(prior)

    if recent_eng is None or prior_eng is None or prior_eng <= 0:
        return AdaptiveDecision(
            interval_minutes=fallback,
            reason="insufficient engagement signal — using fallback",
            recent_engagement=recent_eng, prior_engagement=prior_eng,
        )

    trend = (recent_eng - prior_eng) / max(prior_eng, 0.001)
    # Tighten by up to 50% when engagement is up by 50%+; loosen by up to
    # 100% when down by 50%+. Clamped to the bounds.
    if trend >= 0.5:
        new = max(MIN_INTERVAL_MINUTES, int(fallback * 0.5))
        reason = f"engagement up {trend:+.0%} — tightening cadence"
    elif trend >= 0.1:
        new = max(MIN_INTERVAL_MINUTES, int(fallback * 0.75))
        reason = f"engagement up {trend:+.0%} — slight tighten"
    elif trend <= -0.5:
        new = min(MAX_INTERVAL_MINUTES, int(fallback * 2.0))
        reason = f"engagement down {trend:+.0%} — loosening cadence (avoid fatigue)"
    elif trend <= -0.1:
        new = min(MAX_INTERVAL_MINUTES, int(fallback * 1.5))
        reason = f"engagement down {trend:+.0%} — slight loosen"
    else:
        new = fallback
        reason = f"engagement flat ({trend:+.0%}) — holding cadence"

    return AdaptiveDecision(
        interval_minutes=new,
        reason=reason,
        recent_engagement=recent_eng,
        prior_engagement=prior_eng,
    )


# ── helpers ────────────────────────────────────────────────────────────────
def _last_published_at(posts: Iterable[Post]) -> datetime | None:
    times = [p.published_at for p in posts if p.published_at]
    return max(times) if times else None


def _median_engagement(posts: Iterable[Post]) -> float | None:
    """Median engagement value for a slice. We use the metrics dict's
    `engagement_rate` if present, otherwise fall back to a simple
    likes+shares+comments / impressions ratio. Returns None when no
    post in the slice has metrics yet."""
    values: list[float] = []
    for p in posts:
        m = p.metrics or {}
        if not isinstance(m, dict):
            continue
        if isinstance(m.get("engagement_rate"), (int, float)):
            values.append(float(m["engagement_rate"]))
            continue
        impressions = float(m.get("impressions") or m.get("views") or 0)
        if impressions <= 0:
            continue
        interactions = (
            float(m.get("likes") or 0)
            + float(m.get("shares") or m.get("retweets") or 0)
            + float(m.get("comments") or m.get("replies") or 0)
        )
        values.append(interactions / impressions)
    if not values:
        return None
    return statistics.median(values)
