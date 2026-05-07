"""Medium adapter — long-form posts via the Medium API."""
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


@register_plugin("platform", "medium", api_version="1.0", category="long-form")
class MediumPlatform(SocialPlatform):
    display_name = "Medium"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=False, gif=False, document=False,
        threads=False, scheduling=False, analytics=True,
        experimental=True,   # Medium API was deprecated April 2024
    )
    max_text_length = 100000
    max_hashtags = 5    # Medium calls them tags

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="medium"),
            refresh_token=None,
            scopes=("basicProfile", "publishPost"),
            account_id="med_demo",
            account_handle="@demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "title": payload.extra.get("title", payload.text.split("\n", 1)[0][:100]),
            "contentFormat": "markdown",
            "content": payload.text,
            "tags": [h.value.lstrip("#") for h in payload.hashtags][:5],
            "publishStatus": payload.extra.get("publish_status", "draft"),
        }
        external_id = f"med_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://medium.com/@demo/{external_id}",
            raw_response=body,
        )
