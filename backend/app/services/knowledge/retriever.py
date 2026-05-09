"""Retriever — high-level helper that the runner calls during execute.

Hides:
  * embedding the query (via the org's configured LLM)
  * calling the store's vector search
  * formatting the results into a "context block" that drops cleanly
    into the system prompt

Output of ``retrieve_for_prompt()``:

    "Brand-voice examples (most relevant first):\n
     [1] (past_post, sim=0.81) "We just shipped …"\n
     [2] (style_guide, sim=0.74) "Our tone is …"\n
     ..."

That string concatenates to the existing system prompt with no
restructuring required by the Executor or Critique.

Failure handling: any retrieval failure returns an empty string so
the run still proceeds — RAG is enrichment, not a hard dependency.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


async def retrieve_for_prompt(
    *, store, llm, query: str,
    org_id: str, top_k: int = 5,
    kinds: list[str] | None = None,
    max_chars: int = 1800,
) -> str:
    """Embed the query, retrieve the top-K matching chunks, format
    them as a string suitable for prompt injection. Returns the empty
    string when there's nothing to retrieve or any step fails."""
    if store is None or llm is None or not (query or "").strip():
        return ""
    try:
        vectors = await llm.embed([query])
    except NotImplementedError:
        return ""
    except Exception as exc:                                         # noqa: BLE001
        log.info("kb_query_embed_failed", error=str(exc))
        return ""
    if not vectors:
        return ""
    qv = vectors[0]

    try:
        hits = await store.search(
            org_id, qv, top_k=int(top_k), kinds=kinds,
        )
    except Exception as exc:                                         # noqa: BLE001
        log.info("kb_search_failed", error=str(exc))
        return ""
    if not hits:
        return ""

    lines = ["Brand-voice examples (most relevant first):"]
    used = 0
    for i, h in enumerate(hits, start=1):
        snippet = (h.get("content") or "").strip().replace("\n", " ")
        if not snippet:
            continue
        line = (
            f"[{i}] ({h.get('source_kind', 'kb')}, "
            f"sim={float(h.get('similarity', 0)):.2f}) "
            f"\"{snippet[:300]}\""
        )
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
