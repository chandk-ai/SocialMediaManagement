"""Generic webhook trigger.

Any third-party (Zapier, Make, an internal CRM) can POST to
`/api/v1/webhooks/{trigger_id}` with a JSON body. We require an HMAC-signed
`X-SMMS-Signature` header to authenticate the caller.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from app.plugins.registry import register_plugin

from .base import TriggerAdapter, TriggerEvent


@register_plugin("trigger", "webhook", api_version="1.0", category="generic")
class WebhookTrigger(TriggerAdapter):
    display_name = "Generic webhook"
    description = "POST a JSON body with an HMAC signature header to fire this trigger."

    config_schema = {
        "type": "object",
        "required": ["secret"],
        "properties": {
            "secret":      {"type": "string", "title": "HMAC shared secret"},
            "header_name": {"type": "string", "default": "X-SMMS-Signature"},
            "directive_key": {"type": "string", "default": "directive",
                              "title": "Body key holding the user's instruction"},
        },
    }

    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        secret = (self.config.get("secret") or "").encode("utf-8")
        if not secret:
            return False
        header = self.config.get("header_name", "X-SMMS-Signature")
        sent = headers.get(header) or headers.get(header.lower(), "")
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sent.replace("sha256=", ""))

    async def parse(self, payload: dict[str, Any]) -> list[TriggerEvent]:
        directive_key = self.config.get("directive_key", "directive")
        return [TriggerEvent(
            trigger_id=payload.get("trigger_id", ""),
            sender=payload.get("from", "webhook"),
            directive=str(payload.get(directive_key, "")),
            media_urls=list(payload.get("media_urls") or []),
            raw=payload,
        )]
