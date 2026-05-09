"""KnowledgeStore — pluggable backend for the RAG corpus.

In-memory uses a dict + cosine search; Postgres uses pgvector with
``ORDER BY embedding <=> :q`` cosine distance and the ivfflat index.

Both expose the same surface. The retriever (retriever.py) calls
``search()``; the docs API (api/v1/knowledge.py) calls add/list/delete.
"""
from __future__ import annotations

import asyncio
import json
import math
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.services.knowledge.chunker import chunk_text

log = get_logger(__name__)


class KnowledgeStore(ABC):
    @abstractmethod
    async def add_document(
        self, *, org_id: str, title: str, content: str,
        source_kind: str = "manual", source_ref: str | None = None,
        embed_fn=None,                     # async (list[str]) -> list[list[float]]
        metadata: dict | None = None,
    ) -> str: ...

    @abstractmethod
    async def delete_document(self, *, org_id: str, doc_id: str) -> None: ...

    @abstractmethod
    async def list_documents(
        self, org_id: str, *, kinds: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def search(
        self, org_id: str, query_embedding: list[float], *,
        top_k: int = 5, kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...


# ── memory ─────────────────────────────────────────────────────────────
class InMemoryKnowledgeStore(KnowledgeStore):
    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}
        self._chunks: dict[str, list[dict[str, Any]]] = {}  # doc_id → chunks
        self._lock = asyncio.Lock()

    async def add_document(self, *, org_id, title, content,
                            source_kind="manual", source_ref=None,
                            embed_fn=None, metadata=None):
        if not (content or "").strip():
            raise ValueError("empty content")
        async with self._lock:
            doc_id = str(uuid.uuid4())
            self._docs[doc_id] = {
                "id": doc_id, "org_id": str(org_id), "title": title,
                "source_kind": source_kind, "source_ref": source_ref,
                "metadata": dict(metadata or {}),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            chunks = chunk_text(content)
            embeddings: list[list[float]] = []
            if embed_fn is not None and chunks:
                try:
                    embeddings = await embed_fn([c.text for c in chunks])
                except Exception as exc:                             # noqa: BLE001
                    log.warning("kb_embed_failed", error=str(exc))
                    embeddings = []
            stored: list[dict[str, Any]] = []
            for c in chunks:
                stored.append({
                    "id": str(uuid.uuid4()),
                    "doc_id": doc_id,
                    "org_id": str(org_id),
                    "chunk_idx": c.idx,
                    "content": c.text,
                    "embedding": embeddings[c.idx] if c.idx < len(embeddings) else None,
                })
            self._chunks[doc_id] = stored
            return doc_id

    async def delete_document(self, *, org_id, doc_id):
        async with self._lock:
            doc = self._docs.get(doc_id)
            if not doc or doc["org_id"] != str(org_id):
                return
            self._docs.pop(doc_id, None)
            self._chunks.pop(doc_id, None)

    async def list_documents(self, org_id, *, kinds=None, limit=200):
        rows = [
            d for d in self._docs.values()
            if d["org_id"] == str(org_id)
            and (kinds is None or d["source_kind"] in kinds)
        ]
        rows.sort(key=lambda d: d["created_at"], reverse=True)
        # Augment with chunk count.
        out = []
        for d in rows[:limit]:
            out.append({**d, "chunk_count": len(self._chunks.get(d["id"], []))})
        return out

    async def search(self, org_id, query_embedding, *, top_k=5, kinds=None):
        candidates: list[tuple[dict, float]] = []
        kinds_set = set(kinds) if kinds else None
        for doc_id, chunks in self._chunks.items():
            doc = self._docs.get(doc_id)
            if not doc or doc["org_id"] != str(org_id):
                continue
            if kinds_set and doc["source_kind"] not in kinds_set:
                continue
            for ch in chunks:
                emb = ch.get("embedding")
                if emb is None:
                    continue
                sim = _cosine(query_embedding, emb)
                candidates.append(({**ch, "doc_title": doc["title"],
                                     "source_kind": doc["source_kind"]},
                                    sim))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [{**c, "similarity": s} for c, s in candidates[: int(top_k)]]


# ── postgres ───────────────────────────────────────────────────────────
class PostgresKnowledgeStore(KnowledgeStore):
    def __init__(self, session_maker, *, embedding_dim: int = 1536) -> None:
        self._sm = session_maker
        self._dim = int(embedding_dim)

    async def add_document(self, *, org_id, title, content,
                            source_kind="manual", source_ref=None,
                            embed_fn=None, metadata=None):
        if not (content or "").strip():
            raise ValueError("empty content")
        chunks = chunk_text(content)
        embeddings: list[list[float]] = []
        if embed_fn is not None and chunks:
            try:
                embeddings = await embed_fn([c.text for c in chunks])
            except Exception as exc:                                 # noqa: BLE001
                log.warning("kb_embed_failed", error=str(exc))
                embeddings = []

        from sqlalchemy import text
        async with self._sm() as s:
            # Insert document.
            r = await s.execute(text("""
                INSERT INTO smms.knowledge_documents
                  (org_id, title, source_kind, source_ref, metadata)
                VALUES (:org, :title, :kind, :ref, CAST(:md AS JSONB))
                RETURNING id::text
            """), {"org": str(org_id), "title": title, "kind": source_kind,
                   "ref": source_ref, "md": json.dumps(metadata or {})})
            doc_id = r.scalar_one()

            # Insert chunks.
            for c in chunks:
                emb = embeddings[c.idx] if c.idx < len(embeddings) else None
                emb_str = self._fmt_vector(emb) if emb else None
                await s.execute(text("""
                    INSERT INTO smms.knowledge_chunks
                      (org_id, doc_id, chunk_idx, content, embedding)
                    VALUES (:org, :doc, :idx, :content, CAST(:emb AS vector))
                """), {"org": str(org_id), "doc": doc_id, "idx": c.idx,
                       "content": c.text, "emb": emb_str})
            await s.commit()
            return doc_id

    async def delete_document(self, *, org_id, doc_id):
        from sqlalchemy import text
        async with self._sm() as s:
            await s.execute(text("""
                DELETE FROM smms.knowledge_documents
                 WHERE id=:id AND org_id=:org
            """), {"id": doc_id, "org": str(org_id)})
            await s.commit()

    async def list_documents(self, org_id, *, kinds=None, limit=200):
        from sqlalchemy import text
        clauses = ["d.org_id=:org"]
        params: dict[str, Any] = {"org": str(org_id), "lim": int(limit)}
        if kinds:
            clauses.append("d.source_kind = ANY(:kinds)")
            params["kinds"] = list(kinds)
        async with self._sm() as s:
            r = await s.execute(text(f"""
                SELECT d.id::text, d.title, d.source_kind, d.source_ref,
                       d.metadata, d.created_at, d.updated_at,
                       (SELECT COUNT(*) FROM smms.knowledge_chunks c
                          WHERE c.doc_id = d.id) AS chunk_count
                  FROM smms.knowledge_documents d
                 WHERE {' AND '.join(clauses)}
                 ORDER BY d.created_at DESC LIMIT :lim
            """), params)
            return [{
                "id": row[0], "title": row[1], "source_kind": row[2],
                "source_ref": row[3], "metadata": row[4] or {},
                "created_at": row[5].isoformat() if row[5] else None,
                "updated_at": row[6].isoformat() if row[6] else None,
                "chunk_count": int(row[7] or 0),
            } for row in r.fetchall()]

    async def search(self, org_id, query_embedding, *, top_k=5, kinds=None):
        from sqlalchemy import text
        params: dict[str, Any] = {
            "org": str(org_id), "q": self._fmt_vector(query_embedding),
            "lim": int(top_k),
        }
        kind_filter = ""
        if kinds:
            kind_filter = "AND d.source_kind = ANY(:kinds)"
            params["kinds"] = list(kinds)
        async with self._sm() as s:
            r = await s.execute(text(f"""
                SELECT c.id::text, c.doc_id::text, c.chunk_idx, c.content,
                       d.title, d.source_kind,
                       1 - (c.embedding <=> CAST(:q AS vector)) AS similarity
                  FROM smms.knowledge_chunks c
                  JOIN smms.knowledge_documents d ON d.id = c.doc_id
                 WHERE c.org_id=:org AND c.embedding IS NOT NULL
                   {kind_filter}
                 ORDER BY c.embedding <=> CAST(:q AS vector)
                 LIMIT :lim
            """), params)
            return [{
                "id": row[0], "doc_id": row[1], "chunk_idx": int(row[2]),
                "content": row[3], "doc_title": row[4],
                "source_kind": row[5],
                "similarity": float(row[6]),
            } for row in r.fetchall()]

    def _fmt_vector(self, vec):
        if vec is None:
            return None
        # pgvector accepts a string of "[v1,v2,...]" via CAST.
        # Pad/truncate to expected dim.
        v = list(vec)
        if len(v) < self._dim:
            v = v + [0.0] * (self._dim - len(v))
        elif len(v) > self._dim:
            v = v[: self._dim]
        return "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(a[i]) ** 2 for i in range(n)))
    nb = math.sqrt(sum(float(b[i]) ** 2 for i in range(n)))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
