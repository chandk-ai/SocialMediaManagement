"""Facebook Pages adapter — real Graph API publishing.

Posts to ``/{page-id}/feed`` (text + optional link) or ``/{page-id}/photos``
(image). Uses the Page Access Token stored in `config.page_access_token` —
NOT the user access token, because Pages must be published with their own
token. After OAuth, the SMMS extracts page tokens via ``/me/accounts``
(handled in ``app.api.v1.platforms.oauth_callback``).

Required Page in ``config``:
    page_id            – string, the Facebook Page numeric id
    page_access_token  – string, page-scoped access token (long-lived if possible)
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.domain.value_objects.credentials import EncryptedToken, OAuthCredentials
from app.plugins.registry import register_plugin

from .base import (
    PlatformCapabilities,
    PlatformValidationError,
    PostPayload,
    PublishResult,
    SocialPlatform,
)

log = get_logger(__name__)

GRAPH_API = "https://graph.facebook.com/v21.0"


@register_plugin("platform", "facebook", api_version="1.0", category="social")
class FacebookPlatform(SocialPlatform):
    display_name = "Facebook"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=False,
        threads=False, scheduling=True, analytics=True,
    )
    max_text_length = 63206
    max_hashtags = 30

    # ── helpers ────────────────────────────────────────────────────────
    def _page_token(self) -> str:
        # Prefer per-Page token (right scope) over the user token from OAuth.
        return (
            self.config.get("page_access_token")
            or self._access_token()
        )

    def _page_id(self) -> str:
        return (
            self.config.get("page_id")
            or (self.credentials.account_id if self.credentials else "")
        )

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self._page_id():
            raise PlatformValidationError(
                "No Facebook Page selected. Set config.page_id (numeric id) "
                "during OAuth callback or in Platform settings.",
            )
        if not self._page_token():
            raise PlatformValidationError(
                "No Page access token. Reconnect this account so the Page "
                "selector exposes a token.",
            )

    # ── lifecycle ─────────────────────────────────────────────────────
    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        # The full code↔token exchange lives in app.core.oauth (shared across
        # platforms). This method is a no-op now; kept for the abstract contract.
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="facebook"),
            refresh_token=None,
            scopes=("pages_manage_posts", "pages_read_engagement"),
            account_id=self._page_id(),
            account_handle=self.config.get("page_name"),
        )

    # ── publishing ─────────────────────────────────────────────────────
    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        page_id = self._page_id()
        token = self._page_token()
        message = _compose(payload)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Image post → /photos (text goes in `caption`)
            if payload.media and payload.media[0].kind in ("image", "gif"):
                url = f"{GRAPH_API}/{page_id}/photos"
                data: dict[str, Any] = {
                    "caption": message,
                    "url": payload.media[0].url,
                    "access_token": token,
                }
                if payload.scheduled_for:
                    data["published"] = "false"
                    data["scheduled_publish_time"] = int(payload.scheduled_for.timestamp())
                r = await client.post(url, data=data)
            # Video post → /videos
            elif payload.media and payload.media[0].kind == "video":
                url = f"{GRAPH_API}/{page_id}/videos"
                data = {
                    "description": message,
                    "file_url": payload.media[0].url,
                    "access_token": token,
                }
                r = await client.post(url, data=data)
            # Text-only → /feed
            else:
                url = f"{GRAPH_API}/{page_id}/feed"
                data = {"message": message, "access_token": token}
                if payload.scheduled_for:
                    data["published"] = "false"
                    data["scheduled_publish_time"] = int(payload.scheduled_for.timestamp())
                r = await client.post(url, data=data)

            if r.status_code >= 400:
                raise RuntimeError(f"Facebook publish failed [{r.status_code}]: {r.text}")
            body = r.json()

        external_id = body.get("post_id") or body.get("id") or ""
        log.info("facebook_published", page_id=page_id, external_id=external_id)
        return PublishResult(
            external_post_id=external_id,
            url=f"https://facebook.com/{external_id}" if external_id else None,
            raw_response=body,
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        token = self._page_token()
        if not token or not external_post_id:
            return {}
        url = f"{GRAPH_API}/{external_post_id}/insights"
        params = {
            "metric": "post_impressions,post_engaged_users,post_reactions_by_type_total",
            "access_token": token,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as exc:
            log.warning("facebook_metrics_failed", error=str(exc))
            return {}
        out: dict[str, Any] = {}
        for entry in data.get("data", []):
            name = entry.get("name", "")
            values = entry.get("values", [])
            if values:
                out[name] = values[-1].get("value", 0)
        return out

    async def delete(self, external_post_id: str) -> bool:
        token = self._page_token()
        if not token or not external_post_id:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.delete(
                    f"{GRAPH_API}/{external_post_id}",
                    params={"access_token": token},
                )
                return r.status_code == 200
        except httpx.HTTPError:
            return False


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
