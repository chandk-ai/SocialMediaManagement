"""Per-item selection — newsletter pattern.

Every unseen item becomes its own post. Used when each source row is
already a publishable unit (Notion CMS, blog feed, podcast episode list).
The orchestrator runs the agent loop once per chosen item, fanned out to
every target platform.

Hard de-dup against ``smms.source_items.consumed`` is mandatory — without
it, a daily workflow on a slow source will republish the same item every
day.

Configurable: ``max_per_run`` (cap per execution so a backlog of 100 new
RSS items doesn't generate 100 posts at once), ``ascending`` (oldest-first
when true — useful for back-filling).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.domain.value_objects.ids import SourceId
from app.domain.value_objects.selection import (
    ItemMode, SelectionResult, SkipReason,
)
from app.plugins.registry import register_plugin

from .base import SelectionContext, SelectionStrategy


@register_plugin("selection", "per_item", api_version="1.0", category="builtin")
class PerItemStrategy(SelectionStrategy):
    plugin_name = "per_item"
    display_name = "Per item — every new item becomes its own post"
    description = (
        "Newsletter / blog-RSS / Notion-CMS pattern. Each new item runs "
        "the agent loop once with that item as the sole reference, then "
        "fans out to every target platform. Items already consumed in a "
        "prior run are skipped."
    )
    config_schema = {
        "type": "object",
        "properties": {
            "max_per_run": {
                "type": "integer", "minimum": 1, "maximum": 50, "default": 3,
                "title": "Max posts per run",
                "description": (
                    "Hard cap per workflow execution. If a source returns "
                    "100 new items but the cap is 3, only the 3 newest "
                    "(or oldest, if ascending) are processed; the rest "
                    "stay 'new' for the next run."
                ),
            },
            "ascending": {
                "type": "boolean", "default": False,
                "title": "Oldest first (back-fill mode)",
                "description": (
                    "When true, processes the oldest unseen items first. "
                    "Useful for catching up on a feed where you want the "
                    "chronological posts to land in order."
                ),
            },
        },
    }

    async def select(self, ctx: SelectionContext) -> SelectionResult:
        max_per_run = int(self.config.get("max_per_run", 3))
        ascending = bool(self.config.get("ascending", False))

        ordered = sorted(
            ctx.candidates,
            key=lambda it: it.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=not ascending,
        )

        kept = []
        skipped: list[SkipReason] = []
        for item in ordered:
            sid = str(item.metadata.get("source_id", ""))
            key = (sid, item.external_id)
            if key in ctx.consumed_keys:
                skipped.append(SkipReason(
                    source_id=SourceId(sid) if sid else SourceId(""),
                    external_id=item.external_id,
                    title=item.title,
                    reason="already used in a prior run",
                ))
                continue
            if len(kept) >= max_per_run:
                skipped.append(SkipReason(
                    source_id=SourceId(sid) if sid else SourceId(""),
                    external_id=item.external_id,
                    title=item.title,
                    reason=f"deferred to next run (max_per_run={max_per_run})",
                ))
                continue
            kept.append(item)

        order_label = "oldest-first" if ascending else "newest-first"
        return SelectionResult(
            chosen=kept,
            mode=ItemMode.ONE_POST_PER_ITEM,
            rationale=(
                f"per_item ({order_label}): "
                f"{len(kept)} posts queued, "
                f"{sum(1 for s in skipped if 'deferred' in s.reason)} deferred, "
                f"{sum(1 for s in skipped if 'already' in s.reason)} already used"
            ),
            skipped=skipped,
            candidates=len(ctx.candidates),
        )
