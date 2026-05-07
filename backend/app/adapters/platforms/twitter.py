"""X (Twitter) adapter — v2 tweets endpoint."""
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


@register_plugin("platform", "twitter", api_version="1.0", category="microblog")
class TwitterPlatform(SocialPlatform):
    display_name = "X (Twitter)"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=False,
        threads=True, scheduling=False, analytics=True,
    )
    max_text_length = 280
    max_hashtags = 10

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        # Tweets count link previews differently — keep a small safety margin.
        text_with_tags = payload.text + " " + " ".join(h.value for h in payload.hashtags)
        if len(text_with_tags) > self.max_text_length:
            raise PlatformValidationError(
                f"tweet (text + hashtags) is {len(text_with_tags)} chars, max {self.max_text_length}"
            )

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        log.info("twitter_oauth_exchange", code=oauth_code[:6] + "…")
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="twitter"),
            refresh_token=EncryptedToken(b"<refresh>", key_id="twitter"),
            scopes=("tweet.write", "tweet.read", "users.read"),
            account_id="0000000000",
            account_handle="@demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {"text": _compose_text(payload)}
        if payload.media:
            body["media"] = {"media_ids": ["<uploaded-via-v1.1-upload>"]}
        log.info("twitter_publish", chars=len(body["text"]))
        external_id = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        return PublishResult(
            external_post_id=external_id,
            url=f"https://x.com/i/web/status/{external_id}",
            raw_response=body,
        )


def _compose_text(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    text = payload.text.strip()
    if tags:
        text = f"{text} {tags}"
    return text
