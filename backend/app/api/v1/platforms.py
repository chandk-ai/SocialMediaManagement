from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import current_user, get_platform_service, get_plugin_service
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId, PlatformId
from app.plugins.registry import PluginKind
from app.schemas.platforms import (
    PlatformCreate,
    PlatformGroupOut,
    PlatformOut,
)
from app.services.platform_service import PlatformService
from app.services.plugin_service import PluginService

router = APIRouter()


def _to_out(p) -> PlatformOut:
    return PlatformOut(
        id=p.id, plugin_name=p.plugin_name, display_name=p.display_name,
        account_handle=p.account_handle, account_external_id=p.account_external_id,
        status=p.status.value, config=p.config, is_default=p.is_default,
        tags=list(p.tags or []),
        created_at=p.created_at, last_used_at=p.last_used_at,
    )


@router.get("", response_model=list[PlatformOut])
async def list_platforms(
    plugin: str | None = Query(None, description="Filter by plugin (e.g. 'instagram')"),
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
) -> list[PlatformOut]:
    items = await svc.list(OrgId(UUID(user.org_id)), plugin_name=plugin)
    return [_to_out(i) for i in items]


@router.get("/grouped", response_model=list[PlatformGroupOut])
async def list_grouped(
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
    plugins: PluginService = Depends(get_plugin_service),
) -> list[PlatformGroupOut]:
    """Return accounts grouped by plugin — used by the multi-account UI."""
    groups = await svc.grouped(OrgId(UUID(user.org_id)))
    plugin_meta = {p.name: p for p in plugins.list(PluginKind.PLATFORM)}
    out: list[PlatformGroupOut] = []
    for plugin_name, accounts in groups.items():
        meta = plugin_meta.get(plugin_name)
        out.append(PlatformGroupOut(
            plugin_name=plugin_name,
            display_name=meta.display_name if meta else plugin_name,
            accounts=[_to_out(a) for a in accounts],
        ))
    return out


@router.post("", response_model=PlatformOut, status_code=status.HTTP_201_CREATED)
async def create_platform(
    body: PlatformCreate,
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
) -> PlatformOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    p = await svc.create(
        org_id=OrgId(UUID(user.org_id)),
        plugin_name=body.plugin_name,
        display_name=body.display_name,
        account_handle=body.account_handle,
        config=body.config,
        is_default=body.is_default,
        tags=body.tags,
    )
    return _to_out(p)


@router.post("/{platform_id}/oauth/start")
async def oauth_start(
    platform_id: UUID,
    redirect_uri: str | None = None,
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
) -> dict:
    """Build the platform's real OAuth authorize URL with PKCE + state nonce.

    The state token contains the platform_id so the callback knows which
    Platform row to attach the resulting credentials to.
    """
    from app.core.oauth import OAuthError, get_oauth_client
    p = await svc.repo.get(OrgId(UUID(user.org_id)), PlatformId(platform_id))
    if p is None:
        raise HTTPException(status_code=404, detail="platform not found")
    try:
        client = get_oauth_client(p.plugin_name)
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    callback = redirect_uri or f"https://example.com/api/v1/platforms/{platform_id}/oauth/callback"
    url = await client.authorize_url(
        redirect_uri=callback,
        extra_state={"platform_id": str(platform_id), "org_id": user.org_id},
    )
    return {"platform_id": str(platform_id), "authorize_url": url}


@router.get("/{platform_id}/oauth/callback")
async def oauth_callback(
    platform_id: UUID,
    code: str,
    state: str,
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
) -> dict:
    """Exchange the auth code for tokens; persist encrypted on this Platform row."""
    from app.core.oauth import OAuthError, get_oauth_client
    from app.core.secrets import build_token_vault
    from app.domain.value_objects.credentials import EncryptedToken, OAuthCredentials
    from datetime import datetime, timedelta, timezone

    p = await svc.repo.get(OrgId(UUID(user.org_id)), PlatformId(platform_id))
    if p is None:
        raise HTTPException(status_code=404, detail="platform not found")
    try:
        client = get_oauth_client(p.plugin_name)
        token = await client.exchange_code(code=code, state=state)
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    vault = build_token_vault()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=token.expires_in)
        if token.expires_in else None
    )
    creds = OAuthCredentials(
        access_token=EncryptedToken(
            ciphertext=vault.encrypt(token.access_token).ciphertext,
            key_id=p.plugin_name, expires_at=expires_at,
        ),
        refresh_token=(
            EncryptedToken(
                ciphertext=vault.encrypt(token.refresh_token).ciphertext,
                key_id=p.plugin_name,
            ) if token.refresh_token else None
        ),
        scopes=tuple((token.scope or "").split()),
        account_id=token.raw.get("user_id") or token.raw.get("account_id") or "",
        account_handle=token.raw.get("screen_name") or token.raw.get("name"),
    )
    p.mark_connected(creds)
    await svc.repo.update(p)
    return {"platform_id": str(platform_id), "status": p.status.value,
            "account_id": creds.account_id}
