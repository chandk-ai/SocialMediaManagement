"""Facebook Pages adapter — Graph API /<page-id>/feed."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domain.value_objects.credentials import EncryptedToken, OAuthCredentials
from app.plugins.registry import register_plugin

from .base import (
    PlatformCapabilities,
    PostPayload,
    PublishResult,
    SocialPlatform,
)

log = get_logger(__name__)


@register_plugin("platform", "facebook", api_version="1.0", category="social")
class FacebookPlatform(SocialPlatform):
    display_name = "Facebook"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=False,
        threads=False, scheduling=True, analytics=True,
    )
    max_text_length = 63206
    max_hashtags = 30

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        log.info("facebook_oauth_exchange", code=oauth_code[:6] + "…")
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="facebook"),
            refresh_token=None,
            scopes=("pages_manage_posts", "pages_read_engagement"),
            account_id="page_demo",
            account_handle="Demo Page",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {"message": _compose(payload)}
        if payload.scheduled_for:
            body["published"] = False
            body["scheduled_publish_time"] = int(payload.scheduled_for.timestamp())
        log.info("facebook_publish", chars=len(body["message"]))
        external_id = f"page_demo_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://facebook.com/{external_id}",
            raw_response=body,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()
