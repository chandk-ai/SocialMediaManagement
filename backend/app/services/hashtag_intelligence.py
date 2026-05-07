"""HashtagIntelligence — score and surface trending hashtags per platform
and per niche.

This service is intentionally pluggable. The default implementation
analyses the org's own historical posts (`Post.metrics`) to compute a
weighted-score per tag. A `HashtagSource` plugin can be added later to
ingest external trending feeds (X firehose, IG explore, YouTube trending
endpoints) without touching the public API.

The Executor consumes this service when `WorkflowConfig.use_hashtag_intel`
is set, and the API exposes /hashtags/trending for the dashboard.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.logging import get_logger
from app.domain.entities.post import Post, PostStatus
from app.domain.value_objects.ids import OrgId
from app.repositories.ports import PlatformRepository, PostRepository

log = get_logger(__name__)


_TAG_PATTERN = re.compile(r"#([\w\d_]+)", re.UNICODE)


@dataclass(slots=True)
class HashtagInsight:
    tag: str
    plugin_name: str
    use_count: int = 0
    avg_engagement: float = 0.0
    score: float = 0.0
    last_used_at: datetime | None = None
    sample_post_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HashtagSuggestion:
    tag: str
    score: float
    rationale: str


class HashtagIntelligenceService:
    """Aggregates post metrics into per-tag insights and exposes
    `suggest_for(...)` for the Executor."""

    def __init__(
        self,
        post_repo: PostRepository,
        platform_repo: PlatformRepository,
        *,
        lookback_days: int = 60,
        recency_half_life_days: float = 14.0,
        min_uses: int = 2,
    ) -> None:
        self.post_repo = post_repo
        self.platform_repo = platform_repo
        self.lookback_days = lookback_days
        self.recency_half_life_days = recency_half_life_days
        self.min_uses = min_uses

    async def insights_for(
        self,
        org_id: OrgId,
        *,
        plugin_name: str | None = None,
        now: datetime | None = None,
    ) -> list[HashtagInsight]:
        """Compute the full insight table. Filters by plugin if requested."""
        now = now or datetime.utcnow()
        cutoff = now - timedelta(days=self.lookback_days)
        posts = await self.post_repo.list(
            org_id, status=PostStatus.PUBLISHED.value,
        )
        # Pre-resolve plugin per platform_id so we don't re-query in the loop.
        plugin_cache: dict = {}
        for p in posts:
            if p.platform_id not in plugin_cache:
                plat = await self.platform_repo.get(org_id, p.platform_id)
                plugin_cache[p.platform_id] = plat.plugin_name if plat else None

        agg: dict[tuple[str, str], _TagAccumulator] = defaultdict(_TagAccumulator)
        for p in posts:
            if p.published_at is None or p.published_at < cutoff:
                continue
            plug = plugin_cache.get(p.platform_id)
            if not plug:
                continue
            if plugin_name and plug != plugin_name:
                continue
            engagement = _engagement(p.metrics)
            for tag in _all_tags(p):
                key = (tag, plug)
                acc = agg[key]
                acc.use_count += 1
                acc.engagement_total += engagement
                if not acc.last_used_at or (p.published_at and p.published_at > acc.last_used_at):
                    acc.last_used_at = p.published_at
                if len(acc.sample_post_ids) < 5:
                    acc.sample_post_ids.append(str(p.id))

        out: list[HashtagInsight] = []
        for (tag, plug), acc in agg.items():
            if acc.use_count < self.min_uses:
                continue
            avg_eng = acc.engagement_total / acc.use_count
            recency = _recency_weight(acc.last_used_at, now, self.recency_half_life_days)
            popularity = math.log1p(acc.use_count)
            score = avg_eng * recency * popularity
            out.append(HashtagInsight(
                tag=tag,
                plugin_name=plug,
                use_count=acc.use_count,
                avg_engagement=avg_eng,
                score=score,
                last_used_at=acc.last_used_at,
                sample_post_ids=acc.sample_post_ids,
            ))
        out.sort(key=lambda i: i.score, reverse=True)
        return out

    async def suggest_for(
        self,
        org_id: OrgId,
        *,
        plugin_name: str,
        seed_text: str = "",
        limit: int = 10,
        exclude: tuple[str, ...] = (),
    ) -> list[HashtagSuggestion]:
        """Return the top-N tags for this plugin. If `seed_text` is given,
        we boost tags whose related posts share lexical terms with the
        seed (a tiny content-relevance signal)."""
        insights = await self.insights_for(org_id, plugin_name=plugin_name)
        if not insights:
            return []
        seed_tokens = set(_tokenize(seed_text))
        excl = {t.lower().lstrip("#") for t in exclude}
        out: list[HashtagSuggestion] = []
        for insight in insights:
            if insight.tag.lower().lstrip("#") in excl:
                continue
            relevance = 0.0
            if seed_tokens:
                tag_tokens = set(_tokenize(insight.tag))
                overlap = len(seed_tokens & tag_tokens)
                relevance = overlap / max(1, len(tag_tokens))
            score = insight.score * (1.0 + relevance)
            rationale = (
                f"{insight.use_count} uses · avg engagement "
                f"{insight.avg_engagement:.3f}"
                + (f" · matches seed text ({relevance:.2f})" if relevance else "")
            )
            out.append(HashtagSuggestion(
                tag=insight.tag, score=score, rationale=rationale,
            ))
        out.sort(key=lambda s: s.score, reverse=True)
        return out[:limit]


@dataclass(slots=True)
class _TagAccumulator:
    use_count: int = 0
    engagement_total: float = 0.0
    last_used_at: datetime | None = None
    sample_post_ids: list[str] = field(default_factory=list)


def _all_tags(post: Post) -> set[str]:
    out: set[str] = {h.value if hasattr(h, "value") else str(h) for h in post.hashtags}
    out.update(f"#{t}" for t in _TAG_PATTERN.findall(post.text or ""))
    return {t.lower() for t in out if t}


def _engagement(metrics: dict | None) -> float:
    if not metrics:
        return 0.0
    for k in ("engagement_rate", "engagement_pct", "interaction_rate"):
        v = metrics.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    likes = metrics.get("likes") or metrics.get("favorites") or 0
    impressions = metrics.get("impressions") or metrics.get("views") or 0
    if impressions:
        return float(likes) / float(impressions)
    return 0.0


def _recency_weight(
    when: datetime | None, now: datetime, half_life_days: float,
) -> float:
    if when is None:
        return 0.5
    days = (now - when).total_seconds() / 86400.0
    return 0.5 ** (days / max(0.1, half_life_days))


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"[a-zA-Z][a-zA-Z0-9]+", text.lower())
