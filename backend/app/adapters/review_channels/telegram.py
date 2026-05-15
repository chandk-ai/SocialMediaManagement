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
        drafts = message.drafts or []
        # Targets summary — one bullet per draft showing platform +
        # @account_handle so the reviewer immediately sees the full
        # fan-out before reading the text. Falls back to the legacy
        # ``platform_name`` field for snapshots produced before the
        # May 2026 enrichment.
        target_lines = []
        for d in drafts:
            label = _md_escape(
                d.get("display_name")
                or d.get("plugin_name")
                or d.get("platform_name")
                or ""
            )
            handle = d.get("account_handle")
            line = f"• *{label}*"
            if handle:
                line += f" · {_md_escape(str(handle))}"
            target_lines.append(line)
        targets_block = "\n".join(target_lines) if target_lines else ""

        # Per-target draft preview. Show the platform + handle on each
        # tile header so a quick scan tells you which account each
        # excerpt is for. Cap text length per draft to keep the
        # message under Telegram's 4096 char limit even with many
        # targets.
        per_target_chars = 600 if len(drafts) >= 4 else 800
        preview_chunks = []
        for d in drafts[:5]:
            header = _md_escape(
                d.get("display_name")
                or d.get("plugin_name")
                or d.get("platform_name")
                or ""
            )
            if d.get("account_handle"):
                header += f" · {_md_escape(str(d['account_handle']))}"
            body = _md_escape(d.get("text") or "")[:per_target_chars]
            preview_chunks.append(f"*{header}*\n{body}")
        preview = "\n\n".join(preview_chunks)
        if len(drafts) > 5:
            preview += f"\n\n_(+{len(drafts) - 5} more targets)_"

        # Surface the quorum requirement up-front so reviewers know whether
        # one tap is enough or whether they're voting in a group. The
        # message.metadata dict is populated by ReviewService before send.
        meta = getattr(message, "metadata", None) or {}
        quorum = int(meta.get("quorum_required", 1) or 1)
        header = _md_escape(message.headline)
        if quorum > 1:
            header += f"\n_Quorum: any {quorum} ✅ to publish · any 1 ❌ to veto_"
        targets_count = len(drafts)
        if targets_count > 1:
            header += f"\n_Publishing to *{targets_count}* accounts. Use the web UI to skip specific ones._"
        text_parts = [f"*{header}*"]
        if targets_block:
            text_parts.append("*Targets:*\n" + targets_block)
        if preview:
            text_parts.append(preview)
        text = "\n\n".join(text_parts)[:4000]
        return {
            "chat_id": recipient,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": _approve_label(quorum, 0), "callback_data": "smms_approve"},
                    {"text": "✏️ Revise",  "callback_data": "smms_revise"},
                    {"text": "❌ Reject",  "callback_data": "smms_reject"},
                ]],
            },
        }


def _approve_label(quorum: int, current: int) -> str:
    """Approve button shows 0/N when quorum > 1, plain ✅ otherwise."""
    if quorum > 1:
        return f"✅ Approve ({current}/{quorum})"
    return "✅ Approve"


def _md_escape(s: str) -> str:
    # Telegram Markdown V1 — only *_`[ need escaping.
    for ch in ("*", "_", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s
