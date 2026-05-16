"""Instagram review channel — sends a DM via the Messenger Platform.

Uses the IG Messaging API:
    POST https://graph.facebook.com/v19.0/me/messages
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import ReviewChannel, ReviewMessage

log = get_logger(__name__)


@register_plugin("review_channel", "instagram", api_version="1.0", category="messaging")
class InstagramReviewChannel(ReviewChannel):
    display_name = "Instagram DM"
    description = "Sends the draft as an Instagram DM with quick-reply approval buttons."

    config_schema = {
        "type": "object",
        "required": ["access_token_env"],
        "properties": {
            "access_token_env": {"type": "string", "default": "INSTAGRAM_ACCESS_TOKEN"},
            "graph_version":    {"type": "string", "default": "v19.0"},
        },
    }

    async def send_for_review(self, recipient: str, message: ReviewMessage) -> str:
        token = os.getenv(self.config.get("access_token_env", "INSTAGRAM_ACCESS_TOKEN"), "")
        version = self.config.get("graph_version", "v19.0")
        body = self._build_body(recipient, message)
        if not token:
            log.info("instagram_review_dry_run", recipient=recipient, drafts=len(message.drafts))
            return f"dry-run:{recipient}"
        url = f"https://graph.facebook.com/{version}/me/messages"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url, json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                data = r.json()
            return data.get("message_id", f"ig-msg:{recipient}")
        except httpx.HTTPError as exc:
            log.warning("instagram_send_failed", error=str(exc))
            return f"failed:{recipient}"

    async def acknowledge(
        self, recipient: str, text: str, *, request_reply: bool = False,
    ) -> None:
        # IG DM doesn't have force_reply equivalent; ignore the hint.
        _ = request_reply
        token = os.getenv(self.config.get("access_token_env", "INSTAGRAM_ACCESS_TOKEN"), "")
        version = self.config.get("graph_version", "v19.0")
        if not token:
            log.info("instagram_ack_dry_run", recipient=recipient, text=text)
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"https://graph.facebook.com/{version}/me/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"recipient": {"id": recipient}, "message": {"text": text}},
                )
        except httpx.HTTPError as exc:
            log.warning("instagram_ack_failed", error=str(exc))

    @staticmethod
    def _build_body(recipient: str, message: ReviewMessage) -> dict[str, Any]:
        preview = "\n\n".join(
            f"{d.get('platform_name','')}: {(d.get('text') or '')[:400]}"
            for d in message.drafts[:3]
        )
        return {
            "recipient": {"id": recipient},
            "message": {
                "text": f"{message.headline}\n\n{preview}\n\nReply: APPROVE / REVISE / REJECT",
                "quick_replies": [
                    {"content_type": "text", "title": "Approve",
                     "payload": "smms_approve"},
                    {"content_type": "text", "title": "Revise",
                     "payload": "smms_revise"},
                    {"content_type": "text", "title": "Reject",
                     "payload": "smms_reject"},
                ],
            },
        }
