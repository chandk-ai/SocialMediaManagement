"""Telegram review channel — sends drafts as a Bot API message with an
inline keyboard for Approve / Revise / Reject.

Outbound endpoint:
    POST https://api.telegram.org/bot{TOKEN}/sendMessage
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.plugins.registry import register_plugin

from .base import ReviewChannel, ReviewMessage

log = get_logger(__name__)


@register_plugin("review_channel", "telegram", api_version="1.0", category="messaging")
class TelegramReviewChannel(ReviewChannel):
    display_name = "Telegram"
    description = "Sends the draft to a Telegram chat with Approve/Revise/Reject buttons."

    config_schema = {
        "type": "object",
        "properties": {
            "bot_token":     {"type": "string",
                              "title": "Bot token (from @BotFather)",
                              "x-secret": True},
            "bot_token_env": {"type": "string",
                              "default": "TELEGRAM_BOT_TOKEN",
                              "title": "Fallback env var if bot_token isn't set inline"},
        },
    }

    def _resolve_token(self) -> str:
        """Per-trigger config wins so SaaS tenants can each have their own
        bot. Falls back to the named env var for self-hosted single-bot
        deployments."""
        inline = (self.config.get("bot_token") or "").strip()
        if inline:
            return inline
        return os.getenv(self.config.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")

    async def send_for_review(self, recipient: str, message: ReviewMessage) -> str:
        token = self._resolve_token()
        body = self._build_body(recipient, message)
        if not token:
            log.info("telegram_review_dry_run",
                     recipient=recipient, drafts=len(message.drafts))
            return f"dry-run:{recipient}"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
                data = r.json()
            return str(((data.get("result") or {}).get("message_id"))
                       or f"tg-msg:{recipient}")
        except httpx.HTTPError as exc:
            log.warning("telegram_send_failed", error=str(exc))
            return f"failed:{recipient}"

    async def acknowledge(self, recipient: str, text: str) -> None:
        token = self._resolve_token()
        if not token:
            log.info("telegram_ack_dry_run", recipient=recipient, text=text)
            return
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": recipient, "text": text, "parse_mode": "Markdown"},
                )
        except httpx.HTTPError as exc:
            log.warning("telegram_ack_failed", error=str(exc))

    @staticmethod
    def _build_body(recipient: str, message: ReviewMessage) -> dict[str, Any]:
        preview = "\n\n".join(
            f"*{_md_escape(d.get('platform_name',''))}*\n{(_md_escape(d.get('text') or ''))[:800]}"
            for d in message.drafts[:3]
        )
        text = f"*{_md_escape(message.headline)}*\n\n{preview}"[:4000]
        return {
            "chat_id": recipient,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ Approve", "callback_data": "smms_approve"},
                    {"text": "✏️ Revise",  "callback_data": "smms_revise"},
                    {"text": "❌ Reject",  "callback_data": "smms_reject"},
                ]],
            },
        }


def _md_escape(s: str) -> str:
    # Telegram Markdown V1 — only *_`[ need escaping.
    for ch in ("*", "_", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s
