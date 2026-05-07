"""Bluesky adapter — AT Protocol."""
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


@register_plugin("platform", "bluesky", api_version="1.0", category="microblog")
class BlueskyPlatform(SocialPlatform):
    display_name = "Bluesky"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=False, gif=False, document=False,
        threads=True, scheduling=False, analytics=False,
        experimental=True,
    )
    max_text_length = 300
    max_hashtags = 10

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="bluesky"),
            refresh_token=EncryptedToken(b"<refresh>", key_id="bluesky"),
            scopes=("com.atproto.repo.createRecord",),
            account_id="bsky_demo",
            account_handle="demo.bsky.social",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "repo": self.config.get("did", "did:plc:demo"),
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": _compose(payload),
                "createdAt": datetime.now(timezone.utc).isoformat(),
            },
        }
        external_id = f"bsky_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://bsky.app/profile/demo.bsky.social/post/{external_id}",
            raw_response=body,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text} {tags}".strip()
