"""LLM API key management endpoints — org-scoped, encrypted at rest.

Route order matters: ``/llm-keys/preferred`` MUST be declared before
``/llm-keys/{provider}`` because FastAPI matches in declaration order, and
``{provider}`` is greedy enough to swallow ``preferred``. Without this order
``PUT /llm-keys/preferred`` ends up hitting ``set_key(provider="preferred")``
with the wrong body shape, which 422s and crashes the frontend.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import (
    current_user,
    get_audit_log_service,
    get_llm_credentials_service,
    get_team_service,
)
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId
from app.services.audit_log import AuditLogService
from app.services.llm_credentials import KNOWN_PROVIDERS, LlmCredentialsService
from app.services.team import TeamService

router = APIRouter()


class KeyBody(BaseModel):
    api_key: str


class PreferenceBody(BaseModel):
    provider: str | None = None
    model: str | None = None


def _require_svc(svc: LlmCredentialsService | None) -> LlmCredentialsService:
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="LLM credentials service not available — backend is in memory mode.",
        )
    return svc


# ── reads ────────────────────────────────────────────────────────────────
@router.get("/llm-keys")
async def list_keys(
    user: Principal = Depends(current_user),
    svc: LlmCredentialsService | None = Depends(get_llm_credentials_service),
) -> dict:
    """Which providers have keys configured + the user's preferred provider.
    Never returns plaintext — only `last_4` for visual identification."""
    s = _require_svc(svc)
    org_id = OrgId(UUID(user.org_id))
    keys = await s.list_providers_with_keys(org_id)
    pref_provider, pref_model = await s.get_preferred(org_id)
    return {
        "providers": [
            {"provider": k.provider, "is_set": k.is_set, "last_4": k.last_4}
            for k in keys
        ],
        "known_providers": list(KNOWN_PROVIDERS),
        "preferred_provider": pref_provider,
        "preferred_model": pref_model,
    }


async def _audit_actor_id(
    user: Principal, team: TeamService | None,
) -> UUID | None:
    """Translate ``Principal.subject`` (Supabase auth UID) to the local
    ``smms.users.id`` so audit_log.actor_id joins cleanly. Falls back to
    None when no local row exists yet — better than logging a foreign
    UUID that won't match anything."""
    if team is None:
        return None
    try:
        return await team.resolve_local_user_id(OrgId(UUID(user.org_id)), user.subject)
    except Exception:                                              # noqa: BLE001
        return None


# ── writes — `preferred` MUST be declared before `{provider}` ───────────
@router.put("/llm-keys/preferred")
async def set_preferred(
    body: PreferenceBody,
    user: Principal = Depends(current_user),
    svc: LlmCredentialsService | None = Depends(get_llm_credentials_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
    team: TeamService | None = Depends(get_team_service),
) -> dict:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    s = _require_svc(svc)
    if body.provider and body.provider not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {body.provider}")
    await s.set_preferred(OrgId(UUID(user.org_id)), body.provider, body.model)
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="llm_key.set_preferred",
            resource_type="llm_provider",
            resource_id=None,
            actor_id=await _audit_actor_id(user, team),
            after={"provider": body.provider, "model": body.model},
        )
    return {"ok": True, "provider": body.provider, "model": body.model}


@router.put("/llm-keys/{provider}")
async def set_key(
    provider: str,
    body: KeyBody,
    user: Principal = Depends(current_user),
    svc: LlmCredentialsService | None = Depends(get_llm_credentials_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
    team: TeamService | None = Depends(get_team_service),
) -> dict:
    if provider == "preferred":
        # Belt-and-suspenders: if anything ever bypasses the route order,
        # surface a clear 400 instead of the confusing "missing api_key" 422.
        raise HTTPException(
            status_code=400,
            detail="Use PUT /llm-keys/preferred (with provider+model) to set the default LLM.",
        )
    if provider not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    s = _require_svc(svc)
    try:
        await s.store_key(OrgId(UUID(user.org_id)), provider, body.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="llm_key.set",
            resource_type="llm_provider",
            actor_id=await _audit_actor_id(user, team),
            # Never log the secret — just a fingerprint of the new key.
            after={"provider": provider, "last_4": body.api_key[-4:]},
        )
    return {"ok": True, "provider": provider}


@router.delete("/llm-keys/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    provider: str,
    user: Principal = Depends(current_user),
    svc: LlmCredentialsService | None = Depends(get_llm_credentials_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
    team: TeamService | None = Depends(get_team_service),
) -> None:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    s = _require_svc(svc)
    await s.remove_key(OrgId(UUID(user.org_id)), provider)
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="llm_key.remove",
            resource_type="llm_provider",
            actor_id=await _audit_actor_id(user, team),
            before={"provider": provider},
        )
