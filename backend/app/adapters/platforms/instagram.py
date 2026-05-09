"""Instagram Business adapter — real Graph API publishing.

IG Business publishing is a two-step dance:

1.  POST  /{ig-user-id}/media       → returns container_id
2.  GET   /{container_id}?fields=status_code  → poll until "FINISHED"
3.  POST  /{ig-user-id}/media_publish?creation_id={container_id}

The IG user id is the *Instagram Business Account id* (different from the
FB Page id). After the OAuth callback we look up
``/{page-id}?fields=instagram_business_account`` to find it and stash it on
``config.ig_user_id``.

Required `config`:
    ig_user_id          – Instagram Business Account id (numeric)
    page_access_token   – Page-scoped access token (covers IG too)
"""
from __future__ import annotations

import asyncio
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


@register_plugin("platform", "instagram", api_version="1.0", category="visual")
class InstagramPlatform(SocialPlatform):
    display_name = "Instagram"
    capabilities = PlatformCapabilities(
        text_only=False,         # IG always needs media
        image=True, video=True, gif=False, document=False,
        threads=False, scheduling=False, analytics=True,
    )
    max_text_length = 2200
    max_hashtags = 30

    # ── helpers ────────────────────────────────────────────────────────
    def _ig_user_id(self) -> str:
        return (
            self.config.get("ig_user_id")
            or self.config.get("instagram_business_account_id")
            or (self.credentials.account_id if self.credentials else "")
        )

    def _token(self) -> str:
        return (
            self.config.get("page_access_token")
            or self._access_token()
        )

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not payload.media:
            raise PlatformValidationError(
                "Instagram requires at least one image or video.",
            )
        if not payload.media[0].url or not payload.media[0].url.startswith("http"):
            raise PlatformValidationError(
                "Instagram media must be hosted on a publicly reachable HTTPS URL "
                "(Meta downloads it server-side).",
            )
        if not self._ig_user_id():
            raise PlatformValidationError(
                "No Instagram Business Account linked. Reconnect this account "
                "and pick the Facebook Page that owns the IG profile.",
            )
        if not self._token():
            raise PlatformValidationError(
                "No access token. Reconnect this account.",
            )

    # ── lifecycle ─────────────────────────────────────────────────────
    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="instagram"),
            refresh_token=None,
            scopes=("instagram_basic", "instagram_content_publish",
                    "pages_show_list", "pages_read_engagement"),
            account_id=self._ig_user_id(),
            account_handle=self.config.get("ig_username"),
        )

    # ── publishing ─────────────────────────────────────────────────────
    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        ig = self._ig_user_id()
        token = self._token()
        caption = _compose(payload)
        media = payload.media[0]

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1 — create the media container.
            create_data: dict[str, Any] = {
                "caption": caption,
                "access_token": token,
            }
            if media.kind == "video":
                create_data["media_type"] = "REELS"      # default to Reels for video
                create_data["video_url"] = media.url
            else:
                create_data["image_url"] = media.url

            r = await client.post(f"{GRAPH_API}/{ig}/media", data=create_data)
            if r.status_code >= 400:
                raise RuntimeError(f"IG container create failed [{r.status_code}]: {r.text}")
            container_id = r.json().get("id", "")
            if not container_id:
                raise RuntimeError(f"IG container create returned no id: {r.text}")

            # Step 2 — poll until the container is FINISHED (videos take time).
            await _wait_for_container(client, container_id, token)

            # Step 3 — publish the container.
            r = await client.post(
                f"{GRAPH_API}/{ig}/media_publish",
                data={"creation_id": container_id, "access_token": token},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"IG publish failed [{r.status_code}]: {r.text}")
            published_id = r.json().get("id", "")
            log.info("instagram_published", ig_user=ig, post_id=published_id)

        return PublishResult(
            external_post_id=published_id,
            url=f"https://www.instagram.com/p/{published_id}/" if published_id else None,
            raw_response={"container_id": container_id},
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        """Pull insights for an IG media. Two API calls — /insights for
        impressions/reach/saved/interactions, and the media node itself
        for like_count/comments_count which Insights doesn't return.
        Maps to the engagement service's standard schema."""
        token = self._token()
        if not token or not external_post_id:
            return {}

        insights: dict[str, Any] = {}
        node: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Insights — impressions/reach/saved/total_interactions.
                ri = await client.get(
                    f"{GRAPH_API}/{external_post_id}/insights",
                    params={
                        "metric": "impressions,reach,saved,total_interactions",
                        "access_token": token,
                    },
                )
                if ri.status_code < 400:
                    for entry in ri.json().get("data", []):
                        values = entry.get("values") or []
                        if values:
                            insights[entry.get("name", "")] = values[-1].get(
                                "value", 0)

                # Media node — like_count + comments_count.
                rn = await client.get(
                    f"{GRAPH_API}/{external_post_id}",
                    params={
                        "fields": "like_count,comments_count,media_type",
                        "access_token": token,
                    },
                )
                if rn.status_code < 400:
                    node = rn.json() or {}
        except httpx.HTTPError as exc:
            log.warning("instagram_metrics_failed", error=str(exc))
            return {"fetch_error": str(exc)[:200]}

        return {
            "likes":       int(node.get("like_count") or 0),
            "comments":    int(node.get("comments_count") or 0),
            "saves":       int(insights.get("saved") or 0),
            "impressions": int(insights.get("impressions") or 0),
            "reach":       int(insights.get("reach") or 0),
            "extra": {
                "total_interactions": int(insights.get("total_interactions") or 0),
                "media_type": node.get("media_type"),
            },
        }


async def _wait_for_container(
    client: httpx.AsyncClient, container_id: str, token: str,
    *, max_wait: float = 60.0, interval: float = 2.0,
) -> None:
    """IG videos can take ~30s to encode. Poll status_code until FINISHED."""
    elapsed = 0.0
    while elapsed < max_wait:
        r = await client.get(
            f"{GRAPH_API}/{container_id}",
            params={"fields": "status_code", "access_token": token},
        )
        if r.status_code < 400:
            status = r.json().get("status_code", "")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise RuntimeError(f"IG container processing failed: {r.text}")
        await asyncio.sleep(interval)
        elapsed += interval
    raise RuntimeError(f"IG container {container_id} did not finish within {max_wait}s")


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n.\n.\n{tags}".strip()
