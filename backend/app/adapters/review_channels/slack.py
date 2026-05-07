"""Slack review channel — DMs the reviewer via incoming webhook or chat.postMessage."""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import ReviewChannel, ReviewMessage

log = get_logger(__name__)


@register_plugin("review_channel", "slack", api_version="1.0", category="messaging")
class SlackReviewChannel(ReviewChannel):
    display_name = "Slack"
    description = "Posts the draft to a Slack channel/DM with approve/revise/reject buttons."

    config_schema = {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "title": "#channel-id or user ID"},
            "bot_token_env": {"type": "string", "default": "SLACK_BOT_TOKEN"},
        },
    }

    async def send_for_review(self, recipient: str, message: ReviewMessage) -> str:
        token = os.getenv(self.config.get("bot_token_env", "SLACK_BOT_TOKEN"), "")
        channel = self.config.get("channel") or recipient
        if not token:
            log.info("slack_review_dry_run", recipient=recipient, drafts=len(message.drafts))
            return f"dry-run:{recipient}"
        body: dict[str, Any] = {
            "channel": channel,
            "text": message.headline,
            "blocks": _blocks(message),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
            return data.get("ts", f"slack:{recipient}")
        except httpx.HTTPError as exc:
            log.warning("slack_send_failed", error=str(exc))
            return f"failed:{recipient}"


def _blocks(message: ReviewMessage) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{message.headline}*"}}
    ]
    for d in message.drafts[:3]:
        out.append({"type": "section", "text": {"type": "mrkdwn",
                    "text": f"*{d.get('platform_name','')}*\n{d.get('text','')[:1000]}"}})
    out.append({"type": "actions", "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "Approve"},
         "value": "approve", "style": "primary",
         "action_id": "smms_approve"},
        {"type": "button", "text": {"type": "plain_text", "text": "Revise"},
         "value": "revise", "action_id": "smms_revise"},
        {"type": "button", "text": {"type": "plain_text", "text": "Reject"},
         "value": "reject", "style": "danger",
         "action_id": "smms_reject"},
    ]})
    return out
