"""Mastodon adapter — real publish via /api/v1/statuses.

Mastodon is federated — each user's account lives on a specific instance
(host). We accept ``instance_url`` + ``access_token`` (no OAuth flow needed
for posting; users generate a token in Preferences → Development).
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


@register_plugin("platform", "mastodon", api_version="1.0", category="microblog")
class MastodonPlatform(SocialPlatform):
    display_name = "Mastodon"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=False,
        threads=True, scheduling=True, analytics=True,
    )
    max_text_length = 500
    max_hashtags = 30

    config_schema = {
        "type": "object",
        "required": ["instance_url", "access_token"],
        "properties": {
            "instance_url": {
                "type": "string",
                "title": "Instance URL",
                "description": "e.g. https://mastodon.social or https://hachyderm.io",
            },
            "access_token": {
                "type": "string", "format": "password",
                "title": "Access token",
                "description": "Preferences → Development → New application → Your access token.",
            },
            "visibility": {
                "type": "string",
                "enum": ["public", "unlisted", "private", "direct"],
                "default": "public",
            },
        },
    }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self.config.get("instance_url"):
            raise PlatformValidationError("instance_url is required (e.g. https://mastodon.social)")
        if not self.config.get("access_token"):
            raise PlatformValidationError("access_token is required")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="mastodon"),
            refresh_token=None,
            scopes=("write:statuses",),
            account_id=self.config.get("instance_url", ""),
            account_handle=None,
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        host = self.config["instance_url"].rstrip("/")
        token = self.config["access_token"]

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            body: dict[str, Any] = {
                "status": _compose(payload),
                "visibility": self.config.get("visibility", "public"),
            }
            r = await client.post(f"{host}/api/v1/statuses", json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"Mastodon publish failed [{r.status_code}]: {r.text}")
            data = r.json()

        log.info("mastodon_published", id=data.get("id"))
        return PublishResult(
            external_post_id=str(data.get("id", "")),
            url=data.get("url"),
            raw_response=data,
        )

    async def fetch_metrics(self, external_post_id: str) -> dict[str, Any]:
        host = self.config.get("instance_url", "").rstrip("/")
        token = self.config.get("access_token")
        if not host or not token or not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"Authorization": f"Bearer {token}"},
            ) as client:
                r = await client.get(f"{host}/api/v1/statuses/{external_post_id}")
                if r.status_code >= 400:
                    return {}
                d = r.json()
        except httpx.HTTPError:
            return {}
        return {
            "favourites": d.get("favourites_count", 0),
            "reblogs":    d.get("reblogs_count", 0),
            "replies":    d.get("replies_count", 0),
        }


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
