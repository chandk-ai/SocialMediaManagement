"""Round-robin selection — fair across sources.

Each scheduled run pulls one (or N) item from each source in rotation,
so no single source dominates the timeline. Useful when sources have
genuinely different cadences (e.g. an RSS feed that fires hourly + a
Notion DB that adds rows weekly) but you want both represented.

Synthesise mode by default: the chosen items collapse into one post per
platform, but the planner sees a balanced cross-section. Flip
``mode = "per_item"`` to generate one post per chosen item.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from app.domain.value_objects.ids import SourceId
from app.domain.value_objects.selection import (
    ItemMode, SelectionResult, SkipReason,
)
from app.plugins.registry import register_plugin

from .base import SelectionContext, SelectionStrategy


@register_plugin("selection", "roundrobin", api_version="1.0", category="builtin")
class RoundRobinStrategy(SelectionStrategy):
    plugin_name = "roundrobin"
    display_name = "Round-robin — one from each source, in rotation"
    description = (
        "Pulls one item from each source per run, cycling through. Stops "
        "any single noisy source from dominating the workflow output. "
        "Skips already-consumed items. Falls back to next-newest if a "
        "source has nothing new."
    )
    config_schema = {
        "type": "object",
        "properties": {
            "items_per_source": {
                "type": "integer", "minimum": 1, "maximum": 10, "default": 1,
                "title": "Items per source per run",
            },
            "mode": {
                "type": "string", "enum": ["synthesize", "per_item"],
                "default": "synthesize",
                "title": "Output mode",
                "description": (
                    "synthesize = one post per platform that mentions all "
                    "chosen items; per_item = each chosen item becomes its "
                    "own post."
                ),
            },
        },
    }

    async def select(self, ctx: SelectionContext) -> SelectionResult:
        items_per_source = int(self.config.get("items_per_source", 1))
        mode_str = (self.config.get("mode") or "synthesize").lower()
        mode = (
            ItemMode.ONE_POST_PER_ITEM if mode_str == "per_item"
            else ItemMode.SYNTHESIZE
        )

        # Bucket candidates by source, sorted newest-first within each bucket.
        buckets: dict[str, list] = defaultdict(list)
        for item in ctx.candidates:
            sid = str(item.metadata.get("source_id", ""))
            buckets[sid].append(item)
        for sid, items in buckets.items():
            items.sort(
                key=lambda it: it.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )

        skipped: list[SkipReason] = []
        kept = []

        # Round-robin: take up to N from each bucket, skipping consumed items.
        # Order across buckets is by source_id so the trace is reproducible.
        for sid in sorted(buckets):
            taken = 0
            for item in buckets[sid]:
                key = (sid, item.external_id)
                if key in ctx.consumed_keys:
                    skipped.append(SkipReason(
                        source_id=SourceId(sid) if sid else SourceId(""),
                        external_id=item.external_id,
                        title=item.title,
                        reason="already used in a prior run",
                    ))
                    continue
                if taken >= items_per_source:
                    skipped.append(SkipReason(
                        source_id=SourceId(sid) if sid else SourceId(""),
                        external_id=item.external_id,
                        title=item.title,
                        reason=f"hit items_per_source={items_per_source}",
                    ))
                    continue
                kept.append(item)
                taken += 1

        return SelectionResult(
            chosen=kept,
            mode=mode,
            rationale=(
                f"roundrobin ({mode.value}): {len(kept)} items from "
                f"{len(buckets)} sources ({items_per_source} per source max)"
            ),
            skipped=skipped,
            candidates=len(ctx.candidates),
        )
