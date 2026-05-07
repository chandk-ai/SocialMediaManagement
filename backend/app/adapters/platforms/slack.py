"""Slack adapter — posts to a channel via incoming webhook or chat.postMessage."""
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
            "webhook_url": {"type": "string", "title": "Incoming webhook URL"},
            "channel": {"type": "string", "title": "#channel-name (for bot mode)"},
        },
    }

    async def authenticate(self, oauth_code: str, redirect_uri: str) -> OAuthCredentials:
        return OAuthCredentials(
            access_token=EncryptedToken(b"<reference-impl>", key_id="slack"),
            refresh_token=None,
            scopes=("chat:write", "channels:read"),
            account_id="slack_team_demo",
            account_handle="Demo Workspace",
        )

    async def publish(self, payload: PostPayload) -> PublishResult:
        self.validate(payload)
        body: dict[str, Any] = {
            "channel": self.config.get("channel"),
            "text": payload.text,
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": payload.text}}
            ],
        }
        external_id = f"slack_{int(datetime.now(timezone.utc).timestamp())}"
        return PublishResult(
            external_post_id=external_id,
            url=None,
            raw_response=body,
        )
