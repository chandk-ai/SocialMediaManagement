"""Relevance selection — embedding-based ranking against the directive.

When a workflow runs with a free-text directive (e.g. "post about today's
product launch"), this strategy ranks candidate items by cosine similarity
between the directive's embedding and each item's title+body embedding.
The top-K most relevant unseen items become the chosen set.

When there's NO directive (schedule-driven runs without trigger context),
the strategy degrades gracefully to "newest unseen" — same as freshness —
because there's nothing to rank against.

Cost discipline: we embed the directive ONCE and each item's
title+body[:1500] ONCE. The LLM provider is the org's already-budget-
guarded one; an over-cap workflow gets the standard 402 even on the
selection step.

Configurable: ``top_k``, ``min_similarity`` (drop items below threshold),
``mode`` (synthesize vs per_item).
"""
from __future__ import annotations

import math

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.domain.value_objects.ids import SourceId
from app.domain.value_objects.selection import (
    ItemMode, SelectionResult, SkipReason,
)
from app.plugins.registry import register_plugin

from .base import SelectionContext, SelectionStrategy

log = get_logger(__name__)


@register_plugin("selection", "relevance", api_version="1.0", category="builtin")
class RelevanceStrategy(SelectionStrategy):
    plugin_name = "relevance"
    display_name = "Relevance — rank by directive similarity"
    description = (
        "Embedding-based ranking against the run's directive (the free-text "
        "instruction from a chat trigger like 'post about today's launch'). "
        "Picks the top-K most semantically similar unseen items. Uses the "
        "workflow's configured LLM provider for embeddings — budget-guarded "
        "and per-tenant. Falls back to freshness when no directive is given."
    )
    needs_llm = True

    config_schema = {
        "type": "object",
        "properties": {
            "top_k": {
                "type": "integer", "minimum": 1, "maximum": 20, "default": 5,
                "title": "Top K most relevant items to keep",
            },
            "min_similarity": {
                "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0,
                "title": "Minimum cosine similarity to qualify",
                "description": (
                    "Items below this similarity to the directive are "
                    "skipped. 0.0 = no floor (rank only). 0.3 is a sensible "
                    "starting point if you find too many off-topic items."
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
        min_sim = float(self.config.get("min_similarity", 0.0))
        mode_str = (self.config.get("mode") or "synthesize").lower()
        mode = (
            ItemMode.ONE_POST_PER_ITEM if mode_str == "per_item"
            else ItemMode.SYNTHESIZE
        )

        # Filter: drop items already consumed.
        unseen = [
            it for it in ctx.candidates
            if (str(it.metadata.get("source_id", "")), it.external_id) not in ctx.consumed_keys
        ]

        # No directive → nothing to rank against. Degrade to freshness-style
        # newest-first selection. Same code path the other strategies take.
        if not (ctx.directive or "").strip():
            return _freshness_fallback(ctx, unseen, top_k, mode)

        # No LLM (e.g. memory backend, no provider configured) → also
        # degrade. Logged so customers see it in the trace.
        if ctx.llm is None:
            log.info("relevance_no_llm_falling_back_to_freshness")
            return _freshness_fallback(ctx, unseen, top_k, mode)

        # Embed the directive + each item once.
        try:
            qtext = ctx.directive.strip()
            doc_texts = [
                _doc_text(it) for it in unseen
            ]
            # Single embed() call with the query first — saves a round-trip
            # if the provider batches.
            vectors = await ctx.llm.embed([qtext, *doc_texts])
        except NotImplementedError:
            log.info("relevance_llm_no_embed_falling_back",
                     provider=type(ctx.llm).__name__)
            return _freshness_fallback(ctx, unseen, top_k, mode)
        except Exception as exc:                                       # noqa: BLE001
            log.warning("relevance_embed_failed_falling_back", error=str(exc))
            return _freshness_fallback(ctx, unseen, top_k, mode)

        if not vectors or len(vectors) < len(unseen) + 1:
            return _freshness_fallback(ctx, unseen, top_k, mode)

        qv = vectors[0]
        scored: list[tuple[SourceItem, float]] = []
        for item, vec in zip(unseen, vectors[1:], strict=False):
            scored.append((item, _cosine(qv, vec)))
        scored.sort(key=lambda x: x[1], reverse=True)

        kept: list[SourceItem] = []
        skipped: list[SkipReason] = []
        for item, sim in scored:
            sid = str(item.metadata.get("source_id", ""))
            if sim < min_sim:
                skipped.append(SkipReason(
                    source_id=SourceId(sid) if sid else SourceId(""),
                    external_id=item.external_id,
                    title=item.title,
                    reason=f"similarity {sim:.2f} below floor {min_sim:.2f}",
                ))
                continue
            if len(kept) >= top_k:
                skipped.append(SkipReason(
                    source_id=SourceId(sid) if sid else SourceId(""),
                    external_id=item.external_id,
                    title=item.title,
                    reason=f"hit top_k={top_k}",
                ))
                continue
            kept.append(item)

        rationale = (
            f"relevance: top {len(kept)}/{len(unseen)} unseen items "
            f"by directive similarity (min={min_sim:.2f}, top_k={top_k})"
        )
        return SelectionResult(
            chosen=kept, mode=mode, rationale=rationale,
            skipped=skipped, candidates=len(ctx.candidates),
        )


# ── helpers ────────────────────────────────────────────────────────────────
def _doc_text(it: SourceItem) -> str:
    """Compact text for embedding. Title carries strong signal so it's
    repeated; body truncated to keep token cost predictable."""
    body = (it.body or "")[:1500]
    return f"{it.title}\n{it.title}\n{body}"


def _cosine(a, b) -> float:
    """Cosine similarity. Handles tuples / lists from any embed() impl;
    returns 0.0 on degenerate vectors so they sort to the bottom."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(a[i]) ** 2 for i in range(n)))
    nb = math.sqrt(sum(float(b[i]) ** 2 for i in range(n)))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _freshness_fallback(
    ctx: SelectionContext, unseen: list[SourceItem], top_k: int, mode: ItemMode,
) -> SelectionResult:
    """When relevance can't run (no directive, no LLM, embed failure) —
    take newest-first and call it out in the rationale so the trace
    explains the fallback."""
    from datetime import datetime, timezone
    ordered = sorted(
        unseen,
        key=lambda it: it.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    kept = ordered[:top_k]
    return SelectionResult(
        chosen=kept, mode=mode,
        rationale=f"relevance fallback: newest-first, top {len(kept)} unseen",
        skipped=[], candidates=len(ctx.candidates),
    )
