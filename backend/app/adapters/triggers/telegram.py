"""Telegram trigger — Bot API webhook.

Setup:
1. Create a bot via @BotFather → save the bot token.
2. POST a webhook URL:
       curl https://api.telegram.org/bot<TOKEN>/setWebhook \
            -d url=https://<your-host>/api/v1/webhooks/telegram/<trigger_id> \
            -d secret_token=<random>            # optional but recommended
3. Paste the same secret_token into this trigger's config — we verify the
   `X-Telegram-Bot-Api-Secret-Token` header on every inbound POST.

Inbound shapes we handle:
* `message`         — direct messages (text), with optional `reply_to_message`
* `callback_query`  — inline-keyboard button presses (Approve/Revise/Reject)
* `channel_post`    — posts in channels the bot is admin of (treated as DMs)
"""
from __future__ import annotations

import hmac
from typing import Any

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import TriggerAdapter, TriggerEvent

log = get_logger(__name__)


@register_plugin("trigger", "telegram", api_version="1.0", category="messaging")
class TelegramTrigger(TriggerAdapter):
    display_name = "Telegram (Bot API)"
    description = (
        "Send a message to your Telegram bot to kick off a workflow; drafts "
        "come back as a Telegram message with Approve / Revise / Reject buttons."
    )
    config_schema = {
        "type": "object",
        "properties": {
            "bot_token":     {"type": "string",
                              "title": "Bot token (from @BotFather)",
                              "x-secret": True},
            "bot_token_env": {"type": "string", "default": "TELEGRAM_BOT_TOKEN",
                              "title": "Fallback env var if bot_token isn't set inline"},
            "secret_token":  {"type": "string",
                              "title": "Secret token (set when calling setWebhook)"},
            "allowed_chat_ids": {"type": "array", "items": {"type": "string"},
                                 "title": "Whitelist of chat ids (empty = any)"},
        },
    }

    # ── signature verification ───────────────────────────────────────
    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        secret = self.config.get("secret_token", "")
        if not secret:
            return True   # optional; the URL itself contains a UUID
        sent = (
            headers.get("X-Telegram-Bot-Api-Secret-Token")
            or headers.get("x-telegram-bot-api-secret-token", "")
        )
        return hmac.compare_digest(secret, sent)

    # ── parsing ──────────────────────────────────────────────────────
    async def parse(self, payload: dict[str, Any]) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []

        # Regular text messages (DM, group, channel_post)
        msg = payload.get("message") or payload.get("channel_post")
        if msg:
            ev = self._parse_message(payload, msg)
            if ev is not None:
                events.append(ev)

        # Inline-keyboard button presses
        cq = payload.get("callback_query")
        if cq:
            events.append(self._parse_callback(payload, cq))

        return events

    # ── helpers ──────────────────────────────────────────────────────
    def _parse_message(self, payload: dict[str, Any], msg: dict[str, Any]) -> TriggerEvent | None:
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if not self._allowed(chat_id):
            log.info("telegram_chat_not_allowed", chat_id=chat_id)
            return None
        text = msg.get("text") or msg.get("caption") or ""
        in_reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        return TriggerEvent(
            trigger_id=payload.get("trigger_id", ""),
            sender=chat_id,
            directive=text,
            media_urls=self._extract_media(msg),
            raw=msg,
            in_reply_to=str(in_reply_to) if in_reply_to is not None else None,
        )

    @staticmethod
    def _parse_callback(payload: dict[str, Any], cq: dict[str, Any]) -> TriggerEvent:
        # Button data is one of "smms_approve" / "smms_revise" / "smms_reject"
        data = cq.get("data", "")
        message = cq.get("message") or {}
        original_msg_id = message.get("message_id")
        chat_id = str((message.get("chat") or cq.get("from") or {}).get("id", ""))
        return TriggerEvent(
            trigger_id=payload.get("trigger_id", ""),
            sender=chat_id,
            directive=_button_to_text(data),
            raw=cq,
            in_reply_to=str(original_msg_id) if original_msg_id is not None else None,
        )

    @staticmethod
    def _extract_media(msg: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for k in ("photo", "video", "document", "audio", "voice"):
            v = msg.get(k)
            if isinstance(v, list) and v:                # photo: array of sizes
                file_id = v[-1].get("file_id")
                if file_id: out.append(f"tg-file:{file_id}")
            elif isinstance(v, dict):
                file_id = v.get("file_id")
                if file_id: out.append(f"tg-file:{file_id}")
        return out

    def _allowed(self, chat_id: str) -> bool:
        allow = self.config.get("allowed_chat_ids") or []
        return not allow or chat_id in {str(x) for x in allow}


_BUTTON_MAP = {
    "smms_approve": "Approve",
    "smms_revise":  "Revise",
    "smms_reject":  "Reject",
}


def _button_to_text(data: str) -> str:
    return _BUTTON_MAP.get(data, data)
