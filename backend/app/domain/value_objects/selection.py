"""Selection-layer value objects.

Used by the SelectionStrategy plugin kind and the orchestrator to express
"what items should the agents work on, and how should they be turned into
posts." Pure data — no DB or LLM calls — so strategies stay easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.domain.entities.source import SourceItem
from app.domain.value_objects.ids import SourceId


class ItemMode(str, Enum):
    """How a SelectionResult should be turned into Posts.

    SYNTHESIZE  — all chosen items collapse into ONE plan with one
                  blueprint per target platform (the historical default).
                  The Planner sees every chosen item and synthesises a
                  single per-platform post that covers them.

    ONE_POST_PER_ITEM — each chosen item becomes its own plan with one
                  blueprint per target platform. The Planner runs N times,
                  N posts per platform are produced. The newsletter
                  pattern; matches Notion CMS-mode behaviour.
    """
    SYNTHESIZE = "synthesize"
    ONE_POST_PER_ITEM = "one_post_per_item"


@dataclass(frozen=True, slots=True)
class SkipReason:
    """An item the strategy considered but decided not to use, with the
    reason — surfaced in the run trace so users can debug 'why didn't
    today's blog post get posted?'."""
    source_id: SourceId
    external_id: str
    title: str
    reason: str


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """The output of a SelectionStrategy.

    chosen      — items the agents should work on, in the order the
                  strategy preferred. Already filtered (de-dup, freshness,
                  etc.) and ranked.
    mode        — synthesise into one post or fan out to one post per item.
    rationale   — short human-readable summary for the run trace
                  (e.g. "freshness: 5 newest unseen items in last 72h").
    skipped     — items considered but not chosen, with reasons. Helps
                  customers understand why a workflow that "should have"
                  posted didn't.
    candidates  — total number of items the strategy considered before
                  filtering. Useful for the trace metric "how busy is this
                  source today".
    """
    chosen: list[SourceItem]
    mode: ItemMode = ItemMode.SYNTHESIZE
    rationale: str = ""
    skipped: list[SkipReason] = field(default_factory=list)
    candidates: int = 0
