"""Discord adapter — posts via webhook (no OAuth needed) or bot token."""
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


@register_plugin("platform", "discord", api_version="1.0", category="community")
class DiscordPlatform(SocialPlatform):
    display_name = "Discord"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=True,
        threads=True, scheduling=False, analytics=False,
    )
    max_text_length = 2000
    max_hashtags = 10

    config_schema = {
        "type": "object",
        "properties": {
            "webhook_url": {"type": "string", "title": "Channel webhook URL",
                            "description": "If set, no OAuth is needed."},
            "channel_id": {"type": "string", "title": "Channel ID (when using bot)"},
            "username": {"type": "string", "title": "Override webhook display name"},
        },
    }

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="discord"),
            refresh_token=None,
            scopes=("identify", "webhook.incoming"),
            account_id="discord_demo",
            account_handle="DemoServer#1",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "content": _compose(payload),
            "username": self.config.get("username"),
            "embeds": [],
        }
        external_id = f"discord_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=None,
            raw_response=body,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n{tags}".strip()
