"""Generic vector database source — covers Pinecone, Chroma, Qdrant, Weaviate.

The user provides a query (text or vector), the adapter dispatches to the
configured backend and yields the top-k matches as `SourceItem`s.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)

SUPPORTED = {"pinecone", "chroma", "qdrant", "weaviate"}


@register_plugin("source", "vector_db", api_version="1.0")
class VectorDBSource(ContentSource):
    display_name = "Vector database"
    description = "Pinecone / Chroma / Qdrant / Weaviate — top-k semantic search."
    config_schema = {
        "type": "object",
        "required": ["backend", "query_text", "index"],
        "properties": {
            "backend":     {"enum": list(SUPPORTED), "title": "Backend"},
            "endpoint":    {"type": "string", "title": "API endpoint / host"},
            "api_key":     {"type": "string", "title": "API key"},
            "index":       {"type": "string", "title": "Index / collection name"},
            "query_text":  {"type": "string", "title": "Query text (will be embedded)"},
            "top_k":       {"type": "integer", "minimum": 1, "maximum": 100, "default": 5},
            "title_field": {"type": "string", "default": "title"},
            "body_field":  {"type": "string", "default": "text"},
            "embeddings_provider": {"type": "string", "default": "openai",
                                    "title": "LLM plugin to embed query (any registered LLM with .embed())"},
        },
    }

    async def connect(self) -> None:
        backend = self.config.get("backend", "")
        if backend not in SUPPORTED:
            raise SourceConnectionError(f"unsupported backend: {backend!r}")
        # Verify the SDK is importable (lazy, per-backend so we don't force install of all four).
        try:
            if backend == "pinecone":
                import pinecone  # noqa: F401
            elif backend == "chroma":
                import chromadb  # noqa: F401
            elif backend == "qdrant":
                import qdrant_client  # noqa: F401
            elif backend == "weaviate":
                import weaviate  # noqa: F401
        except ImportError as exc:                                # pragma: no cover
            raise SourceConnectionError(
                f"{backend} SDK is not installed on the backend.",
            ) from exc

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        backend = self.config["backend"]
        try:
            results = await _dispatch(backend, self.config)
        except Exception as exc:                          # noqa: BLE001
            log.warning("vector_db_query_failed", backend=backend, error=str(exc))
            raise SourceConnectionError(f"{backend} query failed: {exc}") from exc
        title_f = self.config.get("title_field", "title")
        body_f = self.config.get("body_field", "text")
        for r in results:
            md = r.get("metadata", {}) or {}
            yield SourceItem(
                external_id=str(r.get("id", "")),
                title=str(md.get(title_f, "")),
                body=str(md.get(body_f, r.get("text", ""))),
                url=md.get("url"),
                published_at=None,
                metadata={"score": r.get("score"), **md},
            )


async def _dispatch(backend: str, cfg: dict) -> list[dict[str, Any]]:
    """Lazily import the right client and run the query."""
    if backend == "pinecone":
        return await _pinecone(cfg)
    if backend == "chroma":
        return await _chroma(cfg)
    if backend == "qdrant":
        return await _qdrant(cfg)
    if backend == "weaviate":
        return await _weaviate(cfg)
    raise SourceConnectionError(backend)


async def _pinecone(cfg: dict) -> list[dict]:
    from pinecone import Pinecone                          # type: ignore
    pc = Pinecone(api_key=cfg.get("api_key"))
    index = pc.Index(cfg["index"])
    vec = await _embed_query(cfg)
    res = index.query(vector=vec, top_k=int(cfg.get("top_k", 5)), include_metadata=True)
    return [m.to_dict() for m in res.matches]              # type: ignore[attr-defined]


async def _chroma(cfg: dict) -> list[dict]:
    import chromadb                                        # type: ignore
    client = chromadb.HttpClient(host=cfg.get("endpoint", "localhost"), port=8000)
    coll = client.get_collection(cfg["index"])
    res = coll.query(query_texts=[cfg["query_text"]], n_results=int(cfg.get("top_k", 5)))
    out = []
    for ids, docs, meta, dist in zip(res["ids"], res["documents"], res["metadatas"], res["distances"]):
        for i, d, m, s in zip(ids, docs, meta, dist):
            out.append({"id": i, "text": d, "metadata": m, "score": float(1 - s)})
    return out


async def _qdrant(cfg: dict) -> list[dict]:
    from qdrant_client import QdrantClient                  # type: ignore
    cli = QdrantClient(url=cfg.get("endpoint"), api_key=cfg.get("api_key"))
    vec = await _embed_query(cfg)
    pts = cli.search(collection_name=cfg["index"], query_vector=vec,
                     limit=int(cfg.get("top_k", 5)))
    return [{"id": p.id, "score": p.score, "metadata": p.payload or {}} for p in pts]


async def _weaviate(cfg: dict) -> list[dict]:
    import weaviate                                         # type: ignore
    client = weaviate.Client(cfg.get("endpoint"))
    res = (
        client.query.get(cfg["index"], ["title", "text", "url"])
        .with_near_text({"concepts": [cfg["query_text"]]})
        .with_limit(int(cfg.get("top_k", 5)))
        .do()
    )
    objs = res.get("data", {}).get("Get", {}).get(cfg["index"], [])
    return [{"id": o.get("_additional", {}).get("id", ""), "metadata": o, "score": 0.0} for o in objs]


async def _embed_query(cfg: dict) -> list[float]:
    from app.plugins.registry import PluginKind, get_global_registry
    name = cfg.get("embeddings_provider", "openai")
    entry = get_global_registry().get(PluginKind.LLM, name)
    provider = entry.cls()
    out = await provider.embed([cfg["query_text"]])
    return out[0]
