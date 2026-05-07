"""Mastodon adapter — federated network, posts to any instance."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.value_objects.credentials import EncryptedToken, OAuthCredentials
from app.plugins.registry import register_plugin

from .base import (
    PlatformCapabilities,
    PostPayload,
    PublishResult,
    SocialPlatform,
)


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
        "required": ["instance_url"],
        "properties": {
            "instance_url": {"type": "string", "title": "Mastodon instance URL"},
        },
    }

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="mastodon"),
            refresh_token=None,
            scopes=("write:statuses", "read:accounts"),
            account_id="mast_demo",
            account_handle="@demo@mastodon.social",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "status": _compose(payload),
            "visibility": payload.extra.get("visibility", "public"),
            "media_ids": [],
            "scheduled_at": payload.scheduled_for.isoformat() if payload.scheduled_for else None,
        }
        instance = self.config.get("instance_url", "https://mastodon.social").rstrip("/")
        external_id = f"masto_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"{instance}/@demo/{external_id}",
            raw_response=body,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
