"""WhatsApp trigger — Meta WhatsApp Cloud API webhook.

Setup (high-level):
1. In Meta Business: register a phone number, get `phone_number_id` and a
   permanent access token.
2. Configure webhook URL `https://<your-host>/api/v1/webhooks/whatsapp/{trigger_id}`
   and a `verify_token` you also paste into this trigger's config.
3. Subscribe to the `messages` field on the WhatsApp Business Account.

The webhook handler in `app/api/v1/webhooks.py` calls this adapter's
`verify_handshake()` for the GET handshake and `parse()` for inbound messages.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from app.plugins.registry import register_plugin

from .base import TriggerAdapter, TriggerEvent, TriggerError


@register_plugin("trigger", "whatsapp", api_version="1.0", category="messaging")
class WhatsAppTrigger(TriggerAdapter):
    display_name = "WhatsApp (Meta Cloud API)"
    description = (
        "Send a message to your WhatsApp Business number to kick off a workflow; "
        "drafts are sent back to you for approval before posting."
    )
    config_schema = {
        "type": "object",
        "required": ["verify_token"],
        "properties": {
            "phone_number_id": {"type": "string",
                                "title": "WhatsApp phone number ID"},
            "verify_token":    {"type": "string",
                                "title": "Webhook verify token (you pick this)"},
            "app_secret":      {"type": "string",
                                "title": "Meta App secret (for signature verification)"},
            "default_language":{"type": "string", "default": "en"},
        },
    }

    # ── handshake (GET) ───────────────────────────────────────────────
    def verify_handshake(self, query: dict[str, str]) -> str | None:
        """Meta sends a GET with hub.* params on first webhook setup."""
        if (
            query.get("hub.mode") == "subscribe"
            and query.get("hub.verify_token") == self.config.get("verify_token")
        ):
            return query.get("hub.challenge", "")
        return None

    # ── signature verification (POST) ────────────────────────────────
    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        secret = (self.config.get("app_secret") or "").encode("utf-8")
        if not secret:
            return True   # signature optional; still gated by verify_token at setup
        sent = (headers.get("X-Hub-Signature-256")
                or headers.get("x-hub-signature-256")
                or "").removeprefix("sha256=")
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sent)

    # ── parsing ──────────────────────────────────────────────────────
    async def parse(self, payload: dict[str, Any]) -> list[TriggerEvent]:
        if payload.get("object") != "whatsapp_business_account":
            return []

        events: list[TriggerEvent] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {}) or {}
                for msg in value.get("messages", []) or []:
                    sender = msg.get("from", "")
                    text = self._extract_text(msg)
                    media_urls = self._extract_media(msg)
                    in_reply_to = (msg.get("context") or {}).get("id")
                    events.append(TriggerEvent(
                        trigger_id=payload.get("trigger_id", ""),
                        sender=sender,
                        directive=text,
                        media_urls=media_urls,
                        raw=msg,
                        in_reply_to=in_reply_to,
                    ))
        return events

    @staticmethod
    def _extract_text(msg: dict[str, Any]) -> str:
        kind = msg.get("type")
        if kind == "text":
            return (msg.get("text") or {}).get("body", "")
        if kind == "interactive":
            interactive = msg.get("interactive") or {}
            if interactive.get("type") == "button_reply":
                return interactive["button_reply"]["title"]
            if interactive.get("type") == "list_reply":
                return interactive["list_reply"]["title"]
        if kind == "button":   # legacy template buttons
            return (msg.get("button") or {}).get("text", "")
        return ""

    @staticmethod
    def _extract_media(msg: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for k in ("image", "video", "audio", "document"):
            media = msg.get(k)
            if media and (mid := media.get("id")):
                out.append(f"wa-media:{mid}")
        return out
