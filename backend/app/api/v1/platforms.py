from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import (
    current_user,
    get_audit_log_service,
    get_platform_service,
    get_plugin_service,
    get_workflow_service,
)
from app.core.security import Principal
from app.domain.value_objects.ids import OrgId, PlatformId
from app.plugins.registry import PluginKind
from app.schemas.platforms import (
    PlatformCreate,
    PlatformGroupOut,
    PlatformOut,
)
from app.services.audit_log import AuditLogService
from app.services.platform_service import PlatformService
from app.services.plugin_service import PluginService
from app.services.workflow_service import WorkflowService

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
    audit: AuditLogService | None = Depends(get_audit_log_service),
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
    if audit is not None:
        await audit.record(
            org_id=user.org_id,
            action="platform.connect",
            resource_type="platform",
            resource_id=p.id,
            after={
                "plugin_name": p.plugin_name,
                "display_name": p.display_name,
                "account_handle": p.account_handle,
            },
        )
    return _to_out(p)


@router.delete("/{platform_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_platform(
    platform_id: UUID,
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
    audit: AuditLogService | None = Depends(get_audit_log_service),
) -> None:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    org_id = OrgId(UUID(user.org_id))
    # Read the row first so we can record the disconnect with the plugin
    # name + handle — the row is gone after delete().
    existing = await svc.repo.get(org_id, PlatformId(platform_id))
    await svc.repo.delete(org_id, PlatformId(platform_id))
    if audit is not None and existing is not None:
        await audit.record(
            org_id=user.org_id,
            action="platform.remove",
            resource_type="platform",
            resource_id=platform_id,
            before={
                "plugin_name": existing.plugin_name,
                "display_name": existing.display_name,
                "account_handle": existing.account_handle,
            },
        )


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
    """Exchange the auth code for tokens; persist encrypted on this Platform row.

    For Meta-family plugins (facebook, instagram), this also enriches
    ``config`` with the chosen Page id + Page access token + linked IG user id
    by calling ``/me/accounts`` on the Graph API. Without this enrichment,
    the FB / IG adapters can't publish (they need a Page-scoped token).
    """
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

    # Provider-specific post-OAuth enrichment so adapters have what they need.
    enriched_config = dict(p.config or {})
    pages_available: list[dict] = []
    enrichment_error: str | None = None
    if p.plugin_name in ("facebook", "instagram", "threads"):
        try:
            pages_available = await _fetch_meta_pages_and_ig(
                user_token=token.access_token,
                want_instagram=(p.plugin_name == "instagram"),
            )
        except Exception as exc:                                # noqa: BLE001
            # Capture the real reason so the user sees it instead of an
            # empty config and a generic "no IG Business Account linked"
            # PlatformValidationError downstream.
            enrichment_error = f"{type(exc).__name__}: {exc}"[:500]
            pages_available = []
        # Auto-pick if only one Page; otherwise leave for the user to choose
        # via the Settings dialog (Page picker) before publishing.
        if len(pages_available) == 1:
            page = pages_available[0]
            enriched_config["page_id"] = page["id"]
            enriched_config["page_name"] = page.get("name", "")
            if page.get("page_access_token"):
                enriched_config["page_access_token"] = page["page_access_token"]
            if p.plugin_name == "instagram" and page.get("instagram_business_account"):
                enriched_config["ig_user_id"] = page["instagram_business_account"].get("id")
                enriched_config["ig_username"] = page["instagram_business_account"].get("username")
            # For Instagram we must NOT fall back to page_id when no IG
            # is linked — using a Page ID as the IG account id sends
            # POSTs against an invalid object and confuses everyone.
            # For Facebook/Threads, page_id is correct.
            if p.plugin_name == "instagram":
                fallback_account = enriched_config.get("ig_user_id") or ""
            else:
                fallback_account = enriched_config.get("ig_user_id") or page["id"]
            creds = OAuthCredentials(
                access_token=creds.access_token,
                refresh_token=creds.refresh_token,
                scopes=creds.scopes,
                account_id=fallback_account,
                account_handle=enriched_config.get("ig_username") or page.get("name"),
            )

    p.config = enriched_config
    if pages_available:
        # Persist the list so a Page-picker UI can offer it later.
        p.config["__pages_available__"] = pages_available
    if enrichment_error:
        p.config["__enrichment_error__"] = enrichment_error
    p.mark_connected(creds)
    await svc.repo.update(p)

    # Classify the outcome so the UI / API consumer knows what to do
    # next without having to guess from a bare 200.
    setup_status = "ready"
    setup_message = "Account ready to publish."
    needs_picker = False
    if p.plugin_name in ("facebook", "instagram", "threads"):
        ig_required = (p.plugin_name == "instagram")
        has_page = bool(enriched_config.get("page_id"))
        has_ig = bool(enriched_config.get("ig_user_id"))
        if enrichment_error:
            setup_status = "enrichment_failed"
            setup_message = (
                f"Meta API call failed while looking up your Pages: "
                f"{enrichment_error}. Reconnect and grant access to a "
                f"Facebook Page that owns your IG Business / Creator account."
            )
        elif len(pages_available) == 0:
            setup_status = "no_pages"
            setup_message = (
                "Meta returned zero Facebook Pages for your account. "
                + ("To publish to Instagram you need: (1) an IG Business "
                   "or Creator account, (2) linked to a Facebook Page you "
                   "admin. Convert via Instagram app → Settings → Account "
                   "type → Switch to Professional, then link a Page from "
                   "the same screen, then reconnect here."
                   if ig_required else
                   "Make sure you admin at least one Facebook Page, then reconnect.")
            )
        elif len(pages_available) > 1 and not (has_page and (has_ig or not ig_required)):
            setup_status = "needs_page_picker"
            needs_picker = True
            setup_message = (
                f"You admin {len(pages_available)} Facebook Pages. "
                "Open the platform's settings and pick the one that owns "
                + ("the Instagram Business account you want to publish to."
                   if ig_required else "the audience you want to publish to.")
            )
        elif ig_required and not has_ig:
            setup_status = "no_ig_link"
            page_name = enriched_config.get("page_name") or "the selected Page"
            setup_message = (
                f"Connected via Facebook Page '{page_name}', but no "
                "Instagram Business / Creator account is linked to that "
                "Page. Link the IG account inside the Page's Meta Business "
                "Suite (Settings → Linked Accounts), then reconnect."
            )

    return {
        "platform_id": str(platform_id),
        "status": p.status.value,
        "account_id": creds.account_id,
        "pages_available": len(pages_available),
        "needs_picker": needs_picker,
        "setup_status": setup_status,
        "setup_message": setup_message,
    }


async def _fetch_meta_pages_and_ig(*, user_token: str, want_instagram: bool) -> list[dict]:
    """Call /me/accounts → list of Pages with per-Page tokens. For each Page,
    look up its linked Instagram Business Account if requested."""
    import httpx
    from app.core.oauth import _META_API_VERSION  # noqa: PLC2701  (constant)
    base = f"https://graph.facebook.com/{_META_API_VERSION}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{base}/me/accounts",
            params={"access_token": user_token, "fields": "id,name,access_token"},
        )
        r.raise_for_status()
        pages = r.json().get("data", [])
        if want_instagram:
            for page in pages:
                try:
                    ri = await client.get(
                        f"{base}/{page['id']}",
                        params={
                            "access_token": page.get("access_token", user_token),
                            "fields": "instagram_business_account{id,username}",
                        },
                    )
                    if ri.status_code < 400:
                        page["instagram_business_account"] = (
                            ri.json().get("instagram_business_account")
                        )
                except Exception:                                # noqa: BLE001
                    pass
        # Rename keys: Graph uses `access_token`; we want `page_access_token` to
        # be unambiguous in our config dict.
        for page in pages:
            if "access_token" in page:
                page["page_access_token"] = page.pop("access_token")
        return pages


class PagePickBody(BaseModel):
    page_id: str


@router.post("/{platform_id}/page/select")
async def select_page(
    platform_id: UUID,
    body: PagePickBody,
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
) -> dict:
    """When a Meta user has multiple Pages, they pick one with this endpoint.
    Copies the chosen Page's id + token (and linked IG account) into ``config``."""
    p = await svc.repo.get(OrgId(UUID(user.org_id)), PlatformId(platform_id))
    if p is None:
        raise HTTPException(status_code=404, detail="platform not found")
    pages = (p.config or {}).get("__pages_available__") or []
    page = next((x for x in pages if x.get("id") == body.page_id), None)
    if page is None:
        raise HTTPException(status_code=400, detail="page_id not in available list")
    cfg = dict(p.config or {})
    cfg["page_id"] = page["id"]
    cfg["page_name"] = page.get("name", "")
    if page.get("page_access_token"):
        cfg["page_access_token"] = page["page_access_token"]
    ig = page.get("instagram_business_account") or {}
    if p.plugin_name == "instagram" and ig:
        cfg["ig_user_id"] = ig.get("id")
        cfg["ig_username"] = ig.get("username")
    p.config = cfg
    await svc.repo.update(p)
    return {"ok": True, "page_id": page["id"]}


class PlatformUpdateBody(BaseModel):
    display_name: str | None = None
    tags: list[str] | None = None
    is_default: bool | None = None
    config: dict | None = None


@router.patch("/{platform_id}", response_model=PlatformOut)
async def update_platform(
    platform_id: UUID,
    body: PlatformUpdateBody,
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
) -> PlatformOut:
    if not user.role.can_edit():
        raise HTTPException(status_code=403, detail="Editor role required")
    p = await svc.repo.get(OrgId(UUID(user.org_id)), PlatformId(platform_id))
    if p is None:
        raise HTTPException(status_code=404, detail="platform not found")
    if body.display_name is not None: p.display_name = body.display_name
    if body.tags is not None:         p.tags = list(body.tags)
    if body.config is not None:       p.config = {**p.config, **body.config}
    if body.is_default is True:
        # Clear default on other accounts of the same plugin
        for other in await svc.repo.list(OrgId(UUID(user.org_id))):
            if other.id != p.id and other.plugin_name == p.plugin_name and other.is_default:
                other.is_default = False
                await svc.repo.update(other)
        p.is_default = True
    elif body.is_default is False:
        p.is_default = False
    await svc.repo.update(p)
    return _to_out(p)


@router.post("/{platform_id}/test_publish")
async def test_publish(
    platform_id: UUID,
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),
) -> dict:
    """Send a small "Hello from SMMS — please ignore" test post end-to-end so
    the user can verify connectivity before scheduling real workflows."""
    from app.adapters.platforms.base import PlatformNotImplemented, PostPayload
    from app.plugins.registry import PluginKind, get_global_registry
    p = await svc.repo.get(OrgId(UUID(user.org_id)), PlatformId(platform_id))
    if p is None:
        raise HTTPException(status_code=404, detail="platform not found")
    entry = get_global_registry().get(PluginKind.PLATFORM, p.plugin_name)
    adapter = entry.cls(credentials=p.credentials, config=p.config)
    if getattr(adapter.capabilities, "experimental", False):
        return {"ok": False,
                "error": f"{adapter.display_name} is in preview — real publishing not yet wired."}
    payload = PostPayload(
        text="Hello from SMMS — please ignore. (test publish)",
        hashtags=[],
        media=[],
    )
    try:
        result = await adapter.publish(payload)
        return {"ok": True, "external_post_id": result.external_post_id, "url": result.url}
    except PlatformNotImplemented as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:                                     # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/{platform_id}/usage")
async def platform_usage(
    platform_id: UUID,
    user: Principal = Depends(current_user),
    svc: PlatformService = Depends(get_platform_service),       # noqa: ARG001
    workflows: WorkflowService = Depends(get_workflow_service),
) -> dict:
    """Workflows that publish to this account."""
    org_id = OrgId(UUID(user.org_id))
    all_wf = await workflows.list(org_id)
    matched = [
        {"id": str(w.id), "name": w.name, "status": w.status.value}
        for w in all_wf
        if str(platform_id) in [str(p) for p in w.platform_ids]
    ]
    return {"count": len(matched), "workflows": matched}
