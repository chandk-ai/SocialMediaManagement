"""YouTube adapter — videos.insert + Community posts."""
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


@register_plugin("platform", "youtube", api_version="1.0", category="video")
class YouTubePlatform(SocialPlatform):
    display_name = "YouTube"
    capabilities = PlatformCapabilities(
        text_only=False,         # video required for video uploads
        image=False, video=True, gif=False, document=False,
        threads=False, scheduling=True, analytics=True,
    )
    max_text_length = 5000       # description
    max_hashtags = 15

    # Distinguishes "video upload" vs "community post" — set in self.config["mode"]
    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        mode = self.config.get("mode", "video")
        if mode == "video":
            if not payload.media or payload.media[0].kind.value != "video":
                raise PlatformValidationError("YouTube video upload requires a video media asset")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        log.info("youtube_oauth_exchange", code=oauth_code[:6] + "…")
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="youtube"),
            refresh_token=EncryptedToken(b"<refresh>", key_id="youtube"),
            scopes=("https://www.googleapis.com/auth/youtube.upload",),
            account_id="UC_demo_channel",
            account_handle="DemoChannel",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        mode = self.config.get("mode", "video")
        if mode == "community":
            body: dict[str, Any] = {"snippet": {"description": _compose(payload)}}
        else:
            body = {
                "snippet": {
                    "title": payload.extra.get("title", payload.text[:80]),
                    "description": _compose(payload),
                    "tags": [h.value.lstrip("#") for h in payload.hashtags],
                    "categoryId": payload.extra.get("category_id", "22"),
                },
                "status": {
                    "privacyStatus": payload.extra.get("privacy", "private"),
                    "selfDeclaredMadeForKids": False,
                },
            }
        log.info("youtube_publish", mode=mode)
        external_id = f"yt_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://youtube.com/watch?v={external_id}",
            raw_response=body,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
