"""EngagementWeighted — selection strategy that biases toward sources
whose past posts performed best (Pillar 3 closing the loop).

How it works
────────────
1. Filter consumed items, same as freshness.
2. For each unseen item, look up its source's avg engagement from the
   rollup table (via EngagementService.learnings_for_selection).
3. Sort by ``recency_weight * recency + engagement_weight * engagement``,
   take top-K.

Both weights are configurable. Defaults give roughly equal pull, so
new sources don't get starved (they have no engagement history → fall
back to recency-only ranking).

Falls back gracefully:
  * No EngagementService bound → pure recency.
  * Source has no engagement history → score = recency only.
  * All sources have zero history → identical to freshness.

Configurable via ``recency_weight``, ``engagement_weight``, ``top_k``,
``mode``. Tied to a Pillar 3 service injected at orchestrator
construction (``ctx.services``); when missing, the strategy reads the
service via the global DI in deps.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.domain.value_objects.ids import SourceId
from app.domain.value_objects.selection import (
    ItemMode, SelectionResult, SkipReason,
)
from app.plugins.registry import register_plugin

from .base import SelectionContext, SelectionStrategy

log = get_logger(__name__)


@register_plugin("selection", "engagement_weighted",
                 api_version="1.0", category="builtin")
class EngagementWeightedStrategy(SelectionStrategy):
    plugin_name = "engagement_weighted"
    display_name = "Engagement-weighted — recency × past performance"
    description = (
        "Picks items whose source historically produced the most engagement. "
        "Combines recency with rollup-table averages from Pillar 3. New "
        "sources are scored by recency only so they aren't starved."
    )

    config_schema = {
        "type": "object",
        "properties": {
            "top_k": {
                "type": "integer", "minimum": 1, "maximum": 50, "default": 5,
                "title": "Top K items to keep",
            },
            "recency_weight": {
                "type": "number", "minimum": 0.0, "maximum": 10.0,
                "default": 1.0,
                "title": "Recency weight",
                "description": "Higher = newer items rank higher.",
            },
            "engagement_weight": {
                "type": "number", "minimum": 0.0, "maximum": 10.0,
                "default": 1.0,
                "title": "Engagement weight",
                "description": (
                    "Higher = sources with high past engagement rank higher. "
                    "Set to 0 to disable and use pure recency."
                ),
            },
            "mode": {
                "type": "string", "enum": ["synthesize", "per_item"],
                "default": "synthesize",
                "title": "Output mode",
            },
        },
    }

    async def select(self, ctx: SelectionContext) -> SelectionResult:
        top_k = int(self.config.get("top_k", 5))
        rw = float(self.config.get("recency_weight", 1.0))
        ew = float(self.config.get("engagement_weight", 1.0))
        mode_str = (self.config.get("mode") or "synthesize").lower()
        mode = (
            ItemMode.ONE_POST_PER_ITEM if mode_str == "per_item"
            else ItemMode.SYNTHESIZE
        )

        unseen = [
            it for it in ctx.candidates
            if (str(it.metadata.get("source_id", "")), it.external_id)
            not in ctx.consumed_keys
        ]
        if not unseen:
            return SelectionResult(
                chosen=[], mode=mode, candidates=len(ctx.candidates),
                rationale="engagement_weighted: no unseen candidates",
            )

        # Pull engagement history for the involved sources, if a service
        # is reachable. We do this via the global DI to avoid coupling
        # SelectionContext to engagement.
        engagement_by_source: dict[str, float] = {}
        if ew > 0:
            try:
                from app.api.deps import get_engagement_service
                svc = get_engagement_service()
                source_ids = list({
                    str(it.metadata.get("source_id", ""))
                    for it in unseen if it.metadata.get("source_id")
                })
                engagement_by_source = await svc.learnings_for_selection(
                    str(ctx.org_id), source_ids=source_ids,
                )
            except Exception as exc:                                 # noqa: BLE001
                log.info("engagement_weighted_no_history",
                         error=str(exc))
                engagement_by_source = {}

        # Normalise engagement to [0, 1] so the weight is interpretable.
        if engagement_by_source:
            mx = max(engagement_by_source.values()) or 1.0
            engagement_by_source = {k: v / mx
                                     for k, v in engagement_by_source.items()}

        now = datetime.now(timezone.utc)

        def score(item) -> float:
            published = item.published_at or now
            try:
                age_h = max(
                    1.0,
                    (now - published).total_seconds() / 3600.0,
                )
            except Exception:                                        # noqa: BLE001
                age_h = 1.0
            recency = 1.0 / age_h
            sid = str(item.metadata.get("source_id", ""))
            eng = engagement_by_source.get(sid, 0.0)
            return rw * recency + ew * eng

        scored = [(it, score(it)) for it in unseen]
        scored.sort(key=lambda x: x[1], reverse=True)

        kept = [it for it, _ in scored[:top_k]]
        skipped: list[SkipReason] = []
        for it, _s in scored[top_k:]:
            sid = str(it.metadata.get("source_id", ""))
            skipped.append(SkipReason(
                source_id=SourceId(sid) if sid else SourceId(""),
                external_id=it.external_id,
                title=it.title,
                reason=f"hit top_k={top_k}",
            ))

        rationale = (
            f"engagement_weighted: top {len(kept)}/{len(unseen)} "
            f"by {rw:.1f}*recency + {ew:.1f}*engagement"
        )
        return SelectionResult(
            chosen=kept, mode=mode, rationale=rationale,
            skipped=skipped, candidates=len(ctx.candidates),
        )
