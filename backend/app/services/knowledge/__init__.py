"""RAG knowledge base — Pillar 4.

Per-org library of brand-voice examples + style + compliance docs
that the Executor and Critique agents retrieve from at generation
time. The store is pluggable (memory + pgvector); the retriever is
shared.

Public surface:

    KnowledgeStore.add_document(org_id, title, source_kind, content,
                                source_ref=None, chunker_config=None) -> doc_id
    KnowledgeStore.delete_document(org_id, doc_id)
    KnowledgeStore.list_documents(org_id) -> [doc]
    KnowledgeStore.search(org_id, query, *, top_k, filter_kinds) -> [chunk]

Implementation notes:

* Chunking is paragraph-aware with a target size of 500 chars and a
  100-char overlap. Large docs split many ways; tiny docs become a
  single chunk.
* Embedding is delegated to whatever LLM provider is configured for
  the org. We use the same ``embed()`` method the relevance strategy
  uses so a single API key footprint covers both.
* The retrieval injection point is the durable runner's `_phase_execute`
  — the Executor receives the top-K snippets as additional context.
"""
from app.services.knowledge.store import (
    KnowledgeStore,
    InMemoryKnowledgeStore,
    PostgresKnowledgeStore,
)
from app.services.knowledge.chunker import chunk_text
from app.services.knowledge.retriever import retrieve_for_prompt

__all__ = [
    "KnowledgeStore", "InMemoryKnowledgeStore", "PostgresKnowledgeStore",
    "chunk_text", "retrieve_for_prompt",
]
