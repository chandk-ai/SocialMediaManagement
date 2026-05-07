"""Discord adapter — real publishing via webhook or bot token.

The webhook path is much simpler — paste the URL from a server's channel
settings → Integrations → Webhooks. Bot mode uses the public REST API at
``discord.com/api/v10/channels/{id}/messages``.
"""
from __future__ import annotations

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
            "webhook_url": {
                "type": "string", "format": "password",
                "title": "Channel webhook URL",
                "description": "Easiest. Server → Channel settings → Integrations → Webhooks → New Webhook → copy URL.",
            },
            "bot_token": {
                "type": "string", "format": "password",
                "title": "Bot token (alternative to webhook)",
                "description": "Required for chat.postMessage and threads.",
            },
            "channel_id": {
                "type": "string",
                "title": "Channel id (bot mode)",
                "description": "Numeric Discord channel id. Right-click channel → Copy Channel ID (Developer mode on).",
            },
            "username": {
                "type": "string",
                "title": "Override display name (webhook only)",
            },
        },
    }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self.config.get("webhook_url") and not (self.config.get("bot_token") and self.config.get("channel_id")):
            raise PlatformValidationError(
                "Configure either a webhook URL or (bot_token + channel_id).",
            )

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="discord"),
            refresh_token=None,
            scopes=("webhook.incoming",),
            account_id=self.config.get("channel_id", ""),
            account_handle=self.config.get("username"),
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        text = _compose(payload)

        async with httpx.AsyncClient(timeout=15.0) as client:
            if self.config.get("webhook_url"):
                body = {"content": text}
                if self.config.get("username"):
                    body["username"] = self.config["username"]
                # `wait=true` returns the message object so we can capture id + url.
                r = await client.post(
                    f"{self.config['webhook_url']}?wait=true",
                    json=body,
                )
                if r.status_code >= 400:
                    raise RuntimeError(f"Discord webhook failed [{r.status_code}]: {r.text}")
                msg = r.json()
                return PublishResult(
                    external_post_id=str(msg.get("id", "")),
                    url=_discord_url(msg),
                    raw_response=msg,
                )

            # Bot mode
            token = self.config["bot_token"]
            channel_id = self.config["channel_id"]
            r = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {token}"},
                json={"content": text},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"Discord bot post failed [{r.status_code}]: {r.text}")
            msg = r.json()
            log.info("discord_published", channel_id=channel_id, msg_id=msg.get("id"))
            return PublishResult(
                external_post_id=str(msg.get("id", "")),
                url=_discord_url(msg),
                raw_response=msg,
            )


def _compose(payload: PostPayload) -> str:
    tags = " ".join(h.value for h in payload.hashtags)
    return f"{payload.text}\n\n{tags}".strip()


def _discord_url(msg: dict) -> str | None:
    guild_id = msg.get("guild_id")
    channel_id = msg.get("channel_id")
    msg_id = msg.get("id")
    if guild_id and channel_id and msg_id:
        return f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}"
    return None
