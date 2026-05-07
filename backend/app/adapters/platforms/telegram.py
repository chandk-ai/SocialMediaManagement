"""Telegram channel adapter — bot token + chat_id."""
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


@register_plugin("platform", "telegram", api_version="1.0", category="community")
class TelegramPlatform(SocialPlatform):
    display_name = "Telegram"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=True, gif=True, document=True,
        threads=False, scheduling=False, analytics=False,
    )
    max_text_length = 4096
    max_hashtags = 10

    config_schema = {
        "type": "object",
        "required": ["bot_token", "chat_id"],
        "properties": {
            "bot_token": {"type": "string", "title": "Bot token"},
            "chat_id":   {"type": "string", "title": "Chat or channel id (e.g. @mychannel)"},
        },
    }

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        # Telegram uses bot tokens, not OAuth — return wrapped credentials directly.
        return OAuthCredentials(
            access_token=EncryptedToken(b"<bot-token>", key_id="telegram"),
            refresh_token=None,
            scopes=("sendMessage", "sendPhoto"),
            account_id="tg_demo",
            account_handle="@demo",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "chat_id": self.config.get("chat_id", "@demo"),
            "text": _compose(payload),
            "parse_mode": "Markdown",
        }
        external_id = f"tg_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=f"https://t.me/demo/{external_id}",
            raw_response=body,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n{tags}".strip()
