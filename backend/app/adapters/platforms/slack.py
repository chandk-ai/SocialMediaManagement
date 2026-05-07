"""Slack adapter — real publishing via Incoming Webhook or chat.postMessage.

Two modes:
  • Webhook mode (simplest)  — paste an Incoming Webhook URL.
  • Bot mode                 — bot user OAuth token (xoxb-...) + channel id.

Both are real. Webhook is recommended for one-channel use cases; Bot mode
gives you control over multiple channels and threads.
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


@register_plugin("platform", "slack", api_version="1.0", category="community")
class SlackPlatform(SocialPlatform):
    display_name = "Slack"
    capabilities = PlatformCapabilities(
        text_only=True, image=True, video=False, gif=True, document=True,
        threads=True, scheduling=True, analytics=False,
    )
    max_text_length = 40000
    max_hashtags = 0

    config_schema = {
        "type": "object",
        "properties": {
            "webhook_url": {
                "type": "string", "format": "password",
                "title": "Incoming Webhook URL",
                "description": "Easiest path. Create one in Slack: Apps → Incoming Webhooks → Add to Slack.",
            },
            "bot_token": {
                "type": "string", "format": "password",
                "title": "Bot user OAuth token (alternative to webhook)",
                "description": "Starts with xoxb-. Required for chat.postMessage and threads.",
            },
            "channel": {
                "type": "string",
                "title": "Channel id or #name (bot mode)",
                "description": "Required if using bot_token. Use the #name or the channel id (C0123ABCDEF).",
            },
        },
    }

    def validate(self, payload: PostPayload) -> None:
        super().validate(payload)
        if not self.config.get("webhook_url") and not self.config.get("bot_token"):
            raise PlatformValidationError(
                "Configure either an Incoming Webhook URL or a bot token + channel.",
            )
        if self.config.get("bot_token") and not self.config.get("channel"):
            raise PlatformValidationError("channel is required when using bot_token.")

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"", key_id="slack"),
            refresh_token=None,
            scopes=("chat:write", "channels:read"),
            account_id=self.config.get("channel", ""),
            account_handle=self.config.get("channel"),
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        text = _compose(payload)

        # Webhook path — simplest: just POST JSON to the URL.
        if self.config.get("webhook_url"):
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(self.config["webhook_url"], json={"text": text})
                if r.status_code >= 400:
                    raise RuntimeError(f"Slack webhook failed [{r.status_code}]: {r.text}")
            return PublishResult(
                external_post_id="webhook",
                url=None,
                raw_response={"webhook": True},
            )

        # Bot path — chat.postMessage returns ts (= permanent message id).
        token = self.config["bot_token"]
        channel = self.config["channel"]
        async with httpx.AsyncClient(timeout=15.0,
                headers={"Authorization": f"Bearer {token}"}) as client:
            r = await client.post(
                "https://slack.com/api/chat.postMessage",
                json={"channel": channel, "text": text, "unfurl_links": True},
            )
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack chat.postMessage error: {data.get('error', r.text)}")
        ts = data.get("ts", "")
        log.info("slack_published", channel=channel, ts=ts)
        return PublishResult(
            external_post_id=ts,
            url=None,
            raw_response=data,
        )


def _compose(payload: PostPayload) -> str:
    return payload.text.strip()
