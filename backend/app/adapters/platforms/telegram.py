"""Telegram channel adapter — real Bot API publishing.

Uses the standard Bot API at ``api.telegram.org/bot{token}/sendMessage``.
Bot token + chat_id come from the user's config (no OAuth — Telegram bots
authenticate via long-lived token only).
"""
from __future__ import annotations

from typing import Any

import httpx

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
            "bot_token": {"type": "string", "format": "password",
                          "title": "Bot token",
                          "description": "Get from @BotFather: /newbot"},
            "chat_id":   {"type": "string", "title": "Chat / channel id",
                          "description": "@channelusername or numeric id (e.g. -1001234567890)"},
        },
    }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self.config.get("bot_token"):
            raise PlatformValidationError("bot_token is required (get from @BotFather)")
        if not self.config.get("chat_id"):
            raise PlatformValidationError("chat_id is required")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="telegram"),
            refresh_token=None,
            scopes=("sendMessage", "sendPhoto"),
            account_id=str(self.config.get("chat_id", "")),
            account_handle=self.config.get("chat_id"),
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        token = self.config["bot_token"]
        chat_id = self.config["chat_id"]
        text = _compose(payload)

        async with httpx.AsyncClient(timeout=15.0) as client:
            if payload.media and payload.media[0].kind == "image":
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={
                        "chat_id": chat_id,
                        "photo": payload.media[0].url,
                        "caption": text,
                        "parse_mode": "HTML",
                    },
                )
            elif payload.media and payload.media[0].kind == "video":
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendVideo",
                    data={
                        "chat_id": chat_id,
                        "video": payload.media[0].url,
                        "caption": text,
                        "parse_mode": "HTML",
                    },
                )
            else:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": "false",
                    },
                )
            if r.status_code >= 400:
                raise RuntimeError(f"Telegram publish failed [{r.status_code}]: {r.text}")
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram publish error: {data.get('description', r.text)}")

        result = data["result"]
        message_id = result.get("message_id", "")
        log.info("telegram_published", chat=chat_id, message_id=message_id)
        return PublishResult(
            external_post_id=str(message_id),
            url=_telegram_url(chat_id, message_id),
            raw_response=result,
        )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()


def _telegram_url(chat_id: Any, message_id: int) -> str | None:
    """Build a public link if chat_id is a public channel handle."""
    cid = str(chat_id)
    if cid.startswith("@"):
        return f"https://t.me/{cid[1:]}/{message_id}"
    return None
