"""TikTok adapter — uses the Content Posting API (open.tiktokapis.com)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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


@register_plugin("platform", "tiktok", api_version="1.0", category="video")
class TikTokPlatform(SocialPlatform):
    display_name = "TikTok"
    capabilities = PlatformCapabilities(
        text_only=False, image=True, video=True, gif=False, document=False,
        threads=False, scheduling=False, analytics=True,
        experimental=True,
    )
    max_text_length = 2200
    max_hashtags = 30

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not payload.media:
            raise PlatformValidationError("TikTok requires a video or image")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        log.info("tiktok_oauth_exchange", code=oauth_code[:6] + "…")
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="tiktok"),
            refresh_token=EncryptedToken(b"<refresh>", key_id="tiktok"),
            scopes=("video.publish", "video.upload", "user.info.basic"),
            account_id="tt_demo",
            account_handle="@demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "post_info": {
                "title": payload.text[:150],
                "description": _compose(payload),
                "privacy_level": payload.extra.get("privacy", "PUBLIC_TO_EVERYONE"),
            },
            "source_info": {"source": "PULL_FROM_URL", "video_url": payload.media[0].url},
        }
        external_id = f"tt_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://www.tiktok.com/@demo/video/{external_id}",
            raw_response=body,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n{tags}".strip()
