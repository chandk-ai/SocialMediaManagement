"""Knowledge-base management endpoints — Pillar 4.

Authoring side:
    POST   /knowledge/documents      add a new document (auto-chunks + embeds)
    GET    /knowledge/documents      list documents in this org
    DELETE /knowledge/documents/{id} remove a document + its chunks

Retrieval side (mostly for debugging — production uses the store
directly from the runner, not via HTTP):
    POST   /knowledge/search         semantic search { query, top_k, kinds }

Auto-ingest of past high-performing posts (engagement-driven) lives
inside the engagement aggregate worker job — not exposed here.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import (
    current_user, get_audit_log_service, get_knowledge_store,
    get_workflow_service,
)
from app.core.security import Principal
from app.services.audit_log import AuditLogService

router = APIRouter()


class DocBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., min_length=1)
    source_kind: str = Field(default="manual",
                              pattern="^(manual|past_post|style_guide|compliance)$")
    source_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/knowledge/documents")
async def add_document(
    body: DocBody,
    user: Principal = Depends(current_user),
    store=Depends(get_knowledge_store),
    audit: AuditLogService | None = Depends(get_audit_log_service),
    wf_service=Depends(get_workflow_service),
) -> dict:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    if store is None:
        raise HTTPException(status_code=503,
                            detail="Knowledge base not available")
    # Resolve an LLM with embed() support — we use the org-default
    # provider configured via LlmCredentialsService. Falls back to
    # storing chunks without embeddings; future search returns empty
    # but the doc is still listable.
    embed_fn = await _build_embed_fn(user.org_id, wf_service)

    try:
        doc_id = await store.add_document(
            org_id=user.org_id, title=body.title, content=body.content,
            source_kind=body.source_kind, source_ref=body.source_ref,
            metadata=body.metadata, embed_fn=embed_fn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if audit:
        await audit.record(
            org_id=user.org_id, action="knowledge.add",
            resource_type="knowledge_document", resource_id=UUID(doc_id),
            after={"title": body.title, "kind": body.source_kind},
        )
    return {"id": doc_id, "embedded": embed_fn is not None}


@router.get("/knowledge/documents")
async def list_documents(
    kinds: str | None = Query(None,
                               description="Comma-separated source_kinds"),
    limit: int = Query(200, ge=1, le=2000),
    user: Principal = Depends(current_user),
    store=Depends(get_knowledge_store),
) -> dict:
    if store is None:
        return {"documents": [], "total": 0}
    parsed = [k.strip() for k in (kinds or "").split(",") if k.strip()] or None
    rows = await store.list_documents(user.org_id, kinds=parsed, limit=int(limit))
    return {"documents": rows, "total": len(rows)}


@router.delete("/knowledge/documents/{doc_id}")
async def delete_document(
    doc_id: UUID,
    user: Principal = Depends(current_user),
    store=Depends(get_knowledge_store),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> dict:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    if store is None:
        raise HTTPException(status_code=503, detail="KB not available")
    await store.delete_document(org_id=user.org_id, doc_id=str(doc_id))
    if audit:
        await audit.record(
            org_id=user.org_id, action="knowledge.delete",
            resource_type="knowledge_document", resource_id=doc_id,
        )
    return {"id": str(doc_id), "deleted": True}


class SearchBody(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=50)
    kinds: list[str] = Field(default_factory=list)


@router.post("/knowledge/search")
async def search(
    body: SearchBody,
    user: Principal = Depends(current_user),
    store=Depends(get_knowledge_store),
    wf_service=Depends(get_workflow_service),
) -> dict:
    if store is None:
        return {"hits": [], "skipped": "no_store"}
    embed_fn = await _build_embed_fn(user.org_id, wf_service)
    if embed_fn is None:
        return {"hits": [], "skipped": "no_llm"}
    vectors = await embed_fn([body.query])
    if not vectors:
        return {"hits": [], "skipped": "no_vector"}
    hits = await store.search(
        user.org_id, vectors[0], top_k=body.top_k,
        kinds=body.kinds or None,
    )
    return {"hits": hits, "total": len(hits)}


# ── helpers ──────────────────────────────────────────────────────────
async def _build_embed_fn(org_id: str, wf_service):
    """Construct an embed-only function over the org's configured LLM.
    Returns None when no provider can be initialised — the store will
    save documents without embeddings (still listable, not searchable
    until re-embedded later)."""
    try:
        from app.api.deps import get_registry
        from app.agents.factory import build_orchestrator
        # Pick any workflow's config as a template — in practice any
        # workflow in the org should have the same provider configured;
        # otherwise we fall back to the first one we find.
        from app.api.deps import _build_repos
        repos = _build_repos()
        wfs = await repos["workflow"].list_for_org(org_id, limit=1)
        if not wfs:
            return None
        wf = wfs[0]
        api_key = await wf_service._resolve_llm_api_key(
            wf.org_id, wf.config.llm_provider)
        orch = build_orchestrator(
            wf, get_registry(), api_key=api_key,
            org_id=org_id, usage_service=None,
            usage_context={"trigger": "knowledge_embed"},
        )
        llm = orch.executor.llm

        async def _embed(texts: list[str]) -> list[list[float]]:
            return await llm.embed(texts)
        return _embed
    except Exception:                                                 # noqa: BLE001
        return None
