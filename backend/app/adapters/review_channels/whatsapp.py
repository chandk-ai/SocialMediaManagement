"""WhatsApp review channel — sends drafts as an interactive WhatsApp message
with Approve / Revise / Reject buttons.

Uses the WhatsApp Cloud API:
    POST https://graph.facebook.com/v19.0/{phone_number_id}/messages
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import ReviewChannel, ReviewMessage

log = get_logger(__name__)


@register_plugin("review_channel", "whatsapp", api_version="1.0", category="messaging")
class WhatsAppReviewChannel(ReviewChannel):
    display_name = "WhatsApp"
    description = "Sends the draft to a reviewer's WhatsApp number with Approve/Revise/Reject buttons."

    config_schema = {
        "type": "object",
        "required": ["phone_number_id"],
        "properties": {
            "phone_number_id": {"type": "string", "title": "Sender phone-number ID"},
            "access_token_env":{"type": "string", "default": "WHATSAPP_ACCESS_TOKEN"},
            "graph_version":   {"type": "string", "default": "v19.0"},
        },
    }

    async def send_for_review(self, recipient: str, message: ReviewMessage) -> str:
        token = os.getenv(self.config.get("access_token_env", "WHATSAPP_ACCESS_TOKEN"), "")
        phone_id = self.config.get("phone_number_id", "")
        version = self.config.get("graph_version", "v19.0")

        body = self._build_interactive_body(recipient, message)
        if not (token and phone_id):
            log.info("whatsapp_review_dry_run",
                     recipient=recipient, drafts=len(message.drafts))
            return f"dry-run:{recipient}"

        url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url, json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                data = r.json()
            return (data.get("messages") or [{}])[0].get("id", f"wa-msg:{recipient}")
        except httpx.HTTPError as exc:
            log.warning("whatsapp_send_failed", error=str(exc))
            return f"failed:{recipient}"

    async def acknowledge(self, recipient: str, text: str) -> None:
        token = os.getenv(self.config.get("access_token_env", "WHATSAPP_ACCESS_TOKEN"), "")
        phone_id = self.config.get("phone_number_id", "")
        version = self.config.get("graph_version", "v19.0")
        if not (token and phone_id):
            log.info("whatsapp_ack_dry_run", recipient=recipient, text=text)
            return
        url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, headers={"Authorization": f"Bearer {token}"},
                                  json={"messaging_product": "whatsapp",
                                        "to": recipient, "type": "text",
                                        "text": {"body": text}})
        except httpx.HTTPError as exc:
            log.warning("whatsapp_ack_failed", error=str(exc))

    @staticmethod
    def _build_interactive_body(recipient: str, message: ReviewMessage) -> dict[str, Any]:
        # WhatsApp interactive button replies cap at 3 buttons + 20-char titles
        preview = "\n\n".join(
            f"*{d.get('platform_name','')}*\n{(d.get('text') or '')[:600]}"
            for d in message.drafts[:3]
        )
        text = f"{message.headline}\n\n{preview}"[:1024]
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text},
                "action": {
                    "buttons": [
                        {"type": "reply",
                         "reply": {"id": "smms_approve", "title": "Approve"}},
                        {"type": "reply",
                         "reply": {"id": "smms_revise", "title": "Revise"}},
                        {"type": "reply",
                         "reply": {"id": "smms_reject", "title": "Reject"}},
                    ]
                },
            },
        }
