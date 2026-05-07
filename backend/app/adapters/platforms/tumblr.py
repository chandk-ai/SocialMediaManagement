"""Tumblr adapter — Neue Post Format."""
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


@register_plugin("platform", "tumblr", api_version="1.0", category="visual")
class TumblrPlatform(SocialPlatform):
    display_name = "Tumblr"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=False,
        threads=False, scheduling=True, analytics=False,
        experimental=True,
    )
    max_text_length = 4096
    max_hashtags = 30

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="tumblr"),
            refresh_token=EncryptedToken(b"<refresh>", key_id="tumblr"),
            scopes=("write",),
            account_id="tumblr_demo",
            account_handle="demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        blog = self.config.get("blog", "demo")
        body: dict[str, Any] = {
            "content": [{"type": "text", "text": payload.text}],
            "tags": [h.value.lstrip("#") for h in payload.hashtags],
            "state": payload.extra.get("state", "published"),
        }
        external_id = f"tumblr_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://{blog}.tumblr.com/post/{external_id}",
            raw_response=body,
        )
