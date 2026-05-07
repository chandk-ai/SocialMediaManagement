"""Pinterest adapter — Pins API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.value_objects.credentials import EncryptedToken, OAuthCredentials
from app.plugins.registry import register_plugin

from .base import (
    PlatformCapabilities,
    PlatformValidationError,
    PostPayload,
    PublishResult,
    SocialPlatform,
)


@register_plugin("platform", "pinterest", api_version="1.0", category="visual")
class PinterestPlatform(SocialPlatform):
    display_name = "Pinterest"
    capabilities = PlatformCapabilities(
        text_only=False, image=True, video=True, gif=False, document=False,
        threads=False, scheduling=False, analytics=True,
    )
    max_text_length = 800
    max_hashtags = 20

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not payload.media:
            raise PlatformValidationError("Pinterest pins require an image or video")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="pinterest"),
            refresh_token=EncryptedToken(b"<refresh>", key_id="pinterest"),
            scopes=("boards:read", "pins:read", "pins:write"),
            account_id="pin_demo",
            account_handle="demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "board_id": self.config.get("board_id", ""),
            "title": payload.extra.get("title", payload.text[:100]),
            "description": payload.text + "\n" + " ".join(h.value for h in payload.hashtags),
            "media_source": {"source_type": "image_url", "url": payload.media[0].url},
        }
        external_id = f"pin_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://pinterest.com/pin/{external_id}",
            raw_response=body,
        )
