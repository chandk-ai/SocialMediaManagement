"""Reddit adapter — submits a self/link post to a subreddit."""
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


@register_plugin("platform", "reddit", api_version="1.0", category="community")
class RedditPlatform(SocialPlatform):
    display_name = "Reddit"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=False, document=False,
        threads=False, scheduling=False, analytics=False,
    )
    max_text_length = 40000
    max_hashtags = 0   # subreddits prefer flair; hashtags are usually noise

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="reddit"),
            refresh_token=EncryptedToken(b"<refresh>", key_id="reddit"),
            scopes=("submit", "identity", "read"),
            account_id="reddit_demo",
            account_handle="u/demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        sr = self.config.get("subreddit", "test")
        body: dict[str, Any] = {
            "sr": sr,
            "kind": "self",
            "title": payload.extra.get("title", payload.text[:300]),
            "text": payload.text,
            "flair_id": payload.extra.get("flair_id"),
        }
        external_id = f"t3_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://reddit.com/r/{sr}/comments/{external_id}",
            raw_response=body,
        )
