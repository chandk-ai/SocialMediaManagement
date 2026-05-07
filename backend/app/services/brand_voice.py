"""BrandVoiceService — index past high-performing posts and retrieve the
top-K closest exemplars to constrain new generation.

Three storage modes:
* `MEMORY`  — embeddings cached in-process (perfect for tests / small orgs)
* `REDIS`   — embeddings persisted in a Redis sorted set (mid-size orgs)
* `VECTOR`  — pushed into the configured vector_db source (Pinecone / Qdrant
              / Chroma / Weaviate) for org-scale corpora

Index keys are `(org_id, plugin_name)` so we always retrieve voice samples
for the same platform we're generating for — LinkedIn voice ≠ TikTok voice.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from app.adapters.llm.base import LLMProvider
from app.core.logging import get_logger
from app.domain.entities.post import Post, PostStatus
from app.domain.value_objects.ids import OrgId

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VoiceExample:
    text: str
    plugin_name: str
    engagement_score: float = 0.0   # used to rank within the top-K
    embedding: tuple[float, ...] | None = None


class BrandVoiceService:
    """In-memory implementation. Swap for a Redis/vector backend by replacing
    `_index` and `_query` with the relevant client calls — the public surface
    stays the same."""

    # Caps the per-org corpus to keep memory bounded; in practice 200 high-
    # performing samples per platform is plenty for retrieval.
    MAX_SAMPLES_PER_PLUGIN = 200

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm
        # {(org_id, plugin_name): [VoiceExample, ...]}
        self._index: dict[tuple[OrgId, str], list[VoiceExample]] = defaultdict(list)

    async def ingest_post(
        self, org_id: OrgId, post: Post, *, plugin_name: str | None = None,
    ) -> None:
        """Persist a published post into the brand-voice corpus. Called by
        the metrics-collector once we have a few days of engagement data."""
        if post.status is not PostStatus.PUBLISHED:
            return
        score = _engagement_score(post)
        if score < 0.2:        # only keep above-baseline posts
            return
        text = post.text.strip()
        if not text:
            return
        try:
            vec = (await self._embed([text]))[0]
        except NotImplementedError:
            vec = _hash_vector(text)
        plug = plugin_name or "default"
        key = (org_id, plug)
        bucket = self._index[key]
        bucket.append(VoiceExample(
            text=text, plugin_name=plug,
            engagement_score=score, embedding=tuple(vec),
        ))
        bucket.sort(key=lambda e: e.engagement_score, reverse=True)
        if len(bucket) > self.MAX_SAMPLES_PER_PLUGIN:
            del bucket[self.MAX_SAMPLES_PER_PLUGIN:]
        log.debug("brand_voice_ingested", org_id=str(org_id),
                  plugin=plug, score=score)

    async def retrieve(
        self, org_id: OrgId, *, plugin_name: str, query: str,
        top_k: int = 5,
    ) -> list[VoiceExample]:
        bucket = self._index.get((org_id, plugin_name), [])
        if not bucket:
            return []
        try:
            qv = (await self._embed([query]))[0]
        except NotImplementedError:
            qv = _hash_vector(query)
        scored = [
            (e, _cosine(qv, list(e.embedding or ())) + 0.1 * e.engagement_score)
            for e in bucket
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [e for e, _ in scored[:top_k]]

    async def render_voice_block(
        self, org_id: OrgId, *, plugin_name: str, query: str,
        top_k: int = 5,
    ) -> str:
        """Builds a system-prompt fragment the Executor splices in to anchor
        generation against past high performers."""
        examples = await self.retrieve(org_id, plugin_name=plugin_name,
                                       query=query, top_k=top_k)
        if not examples:
            return ""
        rendered = "\n---\n".join(
            f"[{e.engagement_score:.2f}] {e.text[:480]}" for e in examples
        )
        return (
            "Match the voice and structure of these high-performing past posts "
            f"(higher score = better real-world engagement):\n{rendered}"
        )

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        return await self.llm.embed(texts)


# ── scoring helpers ────────────────────────────────────────────────────────
def _engagement_score(post: Post) -> float:
    """Map a post's evaluation + (eventually) real metrics to [0, 1].
    Until the performance feedback loop populates `Post.metrics`, we use the
    Evaluator's predicted_engagement as a proxy."""
    if post.evaluation is None:
        return 0.0
    return max(0.0, min(1.0, post.evaluation.predicted_engagement))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)) or 1.0
    nb = math.sqrt(sum(y*y for y in b)) or 1.0
    return dot / (na * nb)


def _hash_vector(text: str, dim: int = 64) -> list[float]:
    """Deterministic fallback when the LLM provider doesn't expose embeddings.
    Hash-based bag-of-tokens — terrible for production, fine for tests."""
    import hashlib
    out = [0.0] * dim
    for tok in text.lower().split():
        h = hashlib.sha1(tok.encode()).digest()
        for i, b in enumerate(h[:8]):
            out[(i * 31 + b) % dim] += 1.0
    norm = math.sqrt(sum(x*x for x in out)) or 1.0
    return [x / norm for x in out]
