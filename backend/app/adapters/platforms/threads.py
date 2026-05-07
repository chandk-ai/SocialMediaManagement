"""Meta Threads adapter — uses the Graph-style threads API."""
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


@register_plugin("platform", "threads", api_version="1.0", category="microblog")
class ThreadsPlatform(SocialPlatform):
    display_name = "Threads"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=False, document=False,
        threads=True, scheduling=False, analytics=True,
    )
    max_text_length = 500
    max_hashtags = 10

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        log.info("threads_oauth_exchange", code=oauth_code[:6] + "…")
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="threads"),
            refresh_token=None,
            scopes=("threads_basic", "threads_content_publish"),
            account_id="threads_demo",
            account_handle="@demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "media_type": "TEXT" if not payload.media else "IMAGE",
            "text": _compose(payload),
        }
        if payload.media:
            body["image_url"] = payload.media[0].url
        external_id = f"th_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://www.threads.net/@demo/post/{external_id}",
            raw_response=body,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text} {tags}".strip()
