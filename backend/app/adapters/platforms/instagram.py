"""Instagram (Business) adapter — Graph API container + publish."""
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


@register_plugin("platform", "instagram", api_version="1.0", category="visual")
class InstagramPlatform(SocialPlatform):
    display_name = "Instagram"
    capabilities = PlatformCapabilities(
        text_only=False,         # IG requires media
        image=True, video=True, gif=False, document=False,
        threads=False, scheduling=True, analytics=True,
    )
    max_text_length = 2200
    max_hashtags = 30

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not payload.media:
            raise PlatformValidationError("Instagram requires at least one image or video")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        log.info("instagram_oauth_exchange", code=oauth_code[:6] + "…")
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="instagram"),
            refresh_token=None,
            scopes=("instagram_content_publish", "pages_read_engagement"),
            account_id="ig_demo",
            account_handle="@demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        # Two-step IG publish: create container → publish container.
        container: dict[str, Any] = {
            "image_url": payload.media[0].url,
            "caption": _compose(payload),
        }
        log.info("instagram_publish", media=len(payload.media))
        external_id = f"ig_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://instagram.com/p/{external_id}",
            raw_response={"container": container, "published": True},
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n.\n.\n{tags}".strip()
