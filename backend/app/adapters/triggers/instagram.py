"""Instagram trigger — Messenger Platform webhook for IG DMs and mentions.

Setup:
1. An Instagram Professional account linked to a Facebook Page.
2. App with `instagram_manage_messages` + `pages_messaging` permissions.
3. Webhook URL `https://<your-host>/api/v1/webhooks/instagram/{trigger_id}`,
   subscribed to the `messages` field on the Instagram object.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from app.plugins.registry import register_plugin

from .base import TriggerAdapter, TriggerEvent


@register_plugin("trigger", "instagram", api_version="1.0", category="messaging")
class InstagramTrigger(TriggerAdapter):
    display_name = "Instagram (DM & mentions)"
    description = (
        "DM your Instagram business account or @-mention it to start a workflow; "
        "drafts are sent back as a DM for approval before posting."
    )
    config_schema = {
        "type": "object",
        "required": ["verify_token"],
        "properties": {
            "verify_token": {"type": "string", "title": "Webhook verify token"},
            "app_secret":   {"type": "string", "title": "Meta App secret"},
            "ig_user_id":   {"type": "string",
                             "title": "Your Instagram user (recipient) ID"},
            "include_mentions": {"type": "boolean", "default": True},
        },
    }

    def verify_handshake(self, query: dict[str, str]) -> str | None:
        if (
            query.get("hub.mode") == "subscribe"
            and query.get("hub.verify_token") == self.config.get("verify_token")
        ):
            return query.get("hub.challenge", "")
        return None

    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        secret = (self.config.get("app_secret") or "").encode("utf-8")
        if not secret:
            return True
        sent = (headers.get("X-Hub-Signature-256")
                or headers.get("x-hub-signature-256")
                or "").removeprefix("sha256=")
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sent)

    async def parse(self, payload: dict[str, Any]) -> list[TriggerEvent]:
        if payload.get("object") != "instagram":
            return []

        events: list[TriggerEvent] = []
        for entry in payload.get("entry", []):
            for msg_event in entry.get("messaging", []) or []:
                sender = (msg_event.get("sender") or {}).get("id", "")
                msg = msg_event.get("message") or {}
                if not msg or msg.get("is_echo"):
                    continue
                text = msg.get("text", "")
                media_urls = [
                    a.get("payload", {}).get("url")
                    for a in (msg.get("attachments") or [])
                    if a.get("payload", {}).get("url")
                ]
                in_reply_to = (msg.get("reply_to") or {}).get("mid")
                events.append(TriggerEvent(
                    trigger_id=payload.get("trigger_id", ""),
                    sender=sender,
                    directive=text,
                    media_urls=[u for u in media_urls if u],
                    raw=msg_event,
                    in_reply_to=in_reply_to,
                ))

            # @-mentions arrive in the `changes` array (different shape)
            if self.config.get("include_mentions", True):
                for change in entry.get("changes", []) or []:
                    if change.get("field") != "mentions":
                        continue
                    val = change.get("value") or {}
                    events.append(TriggerEvent(
                        trigger_id=payload.get("trigger_id", ""),
                        sender=val.get("media_id", ""),
                        directive=val.get("comment_text", ""),
                        raw=change,
                    ))
        return events
