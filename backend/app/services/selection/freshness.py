"""Freshness selection — the sensible default.

Pick the newest unseen items, capped at K per source, optionally bounded
by a freshness window. Synthesise mode: all chosen items collapse into
one Plan with one blueprint per platform. Matches the historical
"first-N items" behaviour but adds:

* hard de-dup against ``smms.source_items.status='consumed'``
* freshness window so a source that goes quiet doesn't keep posting
  yesterday's news
* per-source cap so one chatty source can't drown out the rest

Configurable: ``max_per_source``, ``window_hours``, ``total_cap``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain.value_objects.selection import (
    ItemMode, SelectionResult, SkipReason,
)
from app.domain.value_objects.ids import SourceId
from app.plugins.registry import register_plugin

from .base import SelectionContext, SelectionStrategy


@register_plugin("selection", "freshness", api_version="1.0", category="builtin")
class FreshnessStrategy(SelectionStrategy):
    plugin_name = "freshness"
    display_name = "Freshness — newest unseen items"
    description = (
        "Default strategy. Picks the newest items each source produced "
        "since the last run, drops anything already consumed, caps at "
        "max_per_source per source, and synthesises everything into one "
        "platform-tailored post per target account."
    )
    config_schema = {
        "type": "object",
        "properties": {
            "max_per_source": {
                "type": "integer", "minimum": 1, "maximum": 50, "default": 5,
                "title": "Max items per source",
                "description": (
                    "Caps how many items from any one source feed the "
                    "Planner. Stops a noisy feed from drowning out "
                    "quieter ones."
                ),
            },
            "window_hours": {
                "type": "integer", "minimum": 0, "maximum": 24 * 30, "default": 168,
                "title": "Freshness window (hours)",
                "description": (
                    "Items older than this are skipped. 0 = no limit. "
                    "Default 168h = 7 days."
                ),
            },
            "total_cap": {
                "type": "integer", "minimum": 1, "maximum": 100, "default": 5,
                "title": "Total items handed to the Planner",
                "description": (
                    "After per-source capping, the global top-N by recency."
                ),
            },
        },
    }

    async def select(self, ctx: SelectionContext) -> SelectionResult:
        max_per_source = int(self.config.get("max_per_source", 5))
        window_hours = int(self.config.get("window_hours", 168))
        total_cap = int(self.config.get("total_cap", 5))

        cutoff: datetime | None = None
        if window_hours > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

        skipped: list[SkipReason] = []
        per_source_taken: dict[str, int] = {}
        kept = []

        # Sort newest-first using item.published_at when available.
        ordered = sorted(
            ctx.candidates,
            key=lambda it: it.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        for item in ordered:
            sid = str(item.metadata.get("source_id", ""))
            key = (sid, item.external_id)

            # Filter 1: already consumed (forever de-dup).
            if key in ctx.consumed_keys:
                skipped.append(SkipReason(
                    source_id=SourceId(sid) if sid else SourceId(""),
                    external_id=item.external_id,
                    title=item.title,
                    reason="already used in a prior run",
                ))
                continue

            # Filter 2: outside freshness window.
            if cutoff and item.published_at and item.published_at.replace(tzinfo=timezone.utc) < cutoff:
                skipped.append(SkipReason(
                    source_id=SourceId(sid) if sid else SourceId(""),
                    external_id=item.external_id,
                    title=item.title,
                    reason=f"older than {window_hours}h freshness window",
                ))
                continue

            # Filter 3: per-source cap.
            taken = per_source_taken.get(sid, 0)
            if taken >= max_per_source:
                skipped.append(SkipReason(
                    source_id=SourceId(sid) if sid else SourceId(""),
                    external_id=item.external_id,
                    title=item.title,
                    reason=f"hit max_per_source={max_per_source} for this source",
                ))
                continue

            kept.append(item)
            per_source_taken[sid] = taken + 1
            if len(kept) >= total_cap:
                break

        rationale = (
            f"freshness: kept {len(kept)} of {len(ctx.candidates)} candidates "
            f"(per-source cap {max_per_source}, window {window_hours}h, "
            f"total cap {total_cap})"
        )
        return SelectionResult(
            chosen=kept,
            mode=ItemMode.SYNTHESIZE,
            rationale=rationale,
            skipped=skipped,
            candidates=len(ctx.candidates),
        )
