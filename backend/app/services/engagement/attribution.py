"""Attribution helpers — turn (post, metrics) tuples into ranked
learnings keyed by dimensions of interest.

We compute simple aggregate statistics rather than ML-fancy uplift
attribution: customers want to know "Tuesday 9am LinkedIn posts get
2.3x the engagement of Friday 4pm ones". Avg + sample size + p50 +
p90 fit on a single chart and answer the operator's actual questions.

Engagement score is a normalised weighted sum across metrics so
likes vs replies vs shares aren't mismatched in ranking. Weights
are platform-aware — a YouTube watch_time_s matters more than a
LinkedIn comment-count for ranking VIDEO content.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AttributionDimension(str, Enum):
    SOURCE = "source"
    STRATEGY = "strategy"
    PLATFORM = "platform"
    HOUR = "hour"
    WEEKDAY = "weekday"
    WORKFLOW = "workflow"


@dataclass(slots=True)
class AttributionResult:
    key: str               # e.g. "linkedin", "tuesday-9am", "freshness"
    avg_engagement: float
    p50_engagement: float
    p90_engagement: float
    sample_size: int


# Platform-aware weights — tweak per-platform as you collect data.
DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "linkedin":  {"likes": 1.0, "comments": 4.0, "shares": 6.0,
                   "clicks": 2.0, "impressions": 0.005},
    "twitter":   {"likes": 1.0, "comments": 3.0, "shares": 5.0,
                   "clicks": 1.5, "impressions": 0.001},
    "x":         {"likes": 1.0, "comments": 3.0, "shares": 5.0,
                   "clicks": 1.5, "impressions": 0.001},
    "instagram": {"likes": 1.0, "comments": 3.0, "shares": 5.0,
                   "saves": 4.0, "reach": 0.005},
    "facebook":  {"likes": 1.0, "comments": 3.0, "shares": 5.0,
                   "clicks": 2.0, "reach": 0.005},
    "youtube":   {"likes": 1.0, "comments": 4.0, "plays": 0.5,
                   "watch_time_s": 0.05},
    "tiktok":    {"likes": 1.0, "comments": 3.0, "shares": 5.0,
                   "plays": 0.3},
}


def engagement_score(snapshot: dict[str, Any], platform_kind: str) -> float:
    """Combine snapshot fields into a single comparable score."""
    weights = DEFAULT_WEIGHTS.get(platform_kind.lower(), {
        "likes": 1.0, "comments": 3.0, "shares": 5.0, "clicks": 2.0,
    })
    score = 0.0
    for k, w in weights.items():
        v = snapshot.get(k)
        if v is None:
            continue
        try:
            score += float(v) * float(w)
        except (TypeError, ValueError):
            continue
    return score


def attribute(
    posts_with_snapshots: list[tuple[dict, dict]],
    *, dimension: AttributionDimension,
) -> list[AttributionResult]:
    """Group (post, latest_snapshot) tuples by dimension and produce
    summary stats. Returns sorted descending by avg_engagement."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for post, snap in posts_with_snapshots:
        plat = (post.get("platform_kind") or "").lower()
        score = engagement_score(snap, plat)
        key = _key_for_dim(post, snap, dimension)
        if key:
            buckets[key].append(score)

    out: list[AttributionResult] = []
    for key, scores in buckets.items():
        if not scores:
            continue
        s = sorted(scores)
        out.append(AttributionResult(
            key=key, avg_engagement=statistics.fmean(s),
            p50_engagement=s[len(s) // 2],
            p90_engagement=s[max(0, int(len(s) * 0.9) - 1)],
            sample_size=len(s),
        ))
    out.sort(key=lambda r: r.avg_engagement, reverse=True)
    return out


def _key_for_dim(post, snap, dim: AttributionDimension) -> str | None:
    if dim is AttributionDimension.SOURCE:
        sid = post.get("source_id") or post.get("metadata", {}).get("source_id")
        return str(sid) if sid else None
    if dim is AttributionDimension.STRATEGY:
        return (post.get("strategy") or "").strip().lower() or None
    if dim is AttributionDimension.PLATFORM:
        return (post.get("platform_kind") or "").strip().lower() or None
    if dim is AttributionDimension.WORKFLOW:
        wid = post.get("workflow_id")
        return str(wid) if wid else None
    if dim is AttributionDimension.HOUR:
        ts = post.get("published_at")
        try:
            from datetime import datetime
            d = ts if hasattr(ts, "hour") else datetime.fromisoformat(str(ts))
            return f"h{d.hour:02d}"
        except Exception:                                            # noqa: BLE001
            return None
    if dim is AttributionDimension.WEEKDAY:
        ts = post.get("published_at")
        try:
            from datetime import datetime
            d = ts if hasattr(ts, "weekday") else datetime.fromisoformat(str(ts))
            return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][d.weekday()]
        except Exception:                                            # noqa: BLE001
            return None
    return None
