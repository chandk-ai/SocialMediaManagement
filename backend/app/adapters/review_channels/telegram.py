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
        """Render the draft into Telegram with the best UX shape for its
        content. Order of preference:

          1. Post has a photo and full text fits in 1024 chars →
             single ``sendPhoto`` with photo + caption + Approve/
             Revise/Reject buttons. One notification, one tap, image
             visible above text.
          2. Post has a photo and full text is too long → ``sendPhoto``
             first as a header (no buttons, short cue), then
             ``sendMessage`` with the full text + buttons. Image on
             top, text + decision below.
          3. Post has a video → same shape via ``sendVideo``.
          4. No media → ``sendMessage`` only (original behavior).

        Returns the message_id of the message that carries the inline
        keyboard (last message in the multi-message case), so the
        webhook can correlate callback_query taps and inline replies
        back to the right ReviewSession.
        """
        token = self._resolve_token()
        body = self._build_body(recipient, message)
        if not token:
            log.info("telegram_review_dry_run",
                     recipient=recipient, drafts=len(message.drafts))
            return f"dry-run:{recipient}"

        media = self._first_media(message)
        if media is None:
            # Pure text-only path — unchanged.
            return await self._send(token, "sendMessage", body, recipient)

        # Decide endpoint based on media kind.
        kind = (media.get("kind") or "image").lower()
        is_video = kind == "video"
        endpoint = "sendVideo" if is_video else "sendPhoto"
        param_name = "video" if is_video else "photo"

        full_text = body["text"]
        reply_markup = body["reply_markup"]
        # Telegram caption cap is 1024 chars for both sendPhoto + sendVideo.
        CAPTION_LIMIT = 1024

        if len(full_text) <= CAPTION_LIMIT:
            # One-message happy path — image + full text + buttons,
            # rendered top-to-bottom: media above caption, buttons
            # below. Best UX.
            media_body = {
                "chat_id": recipient,
                param_name: media["url"],
                "caption": full_text,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup,
            }
            return await self._send(token, endpoint, media_body, recipient)

        # Caption would overflow — split into two messages.
        # 1) Media first as a header (no buttons here so the reviewer
        #    doesn't accidentally tap before seeing the text).
        header_caption = "👇 Draft for review — full post below"
        header_body = {
            "chat_id": recipient,
            param_name: media["url"],
            "caption": header_caption,
            "parse_mode": "Markdown",
        }
        await self._send(token, endpoint, header_body, recipient)
        # 2) Full text + buttons — this is the message ref we return,
        #    because callback_query taps + reply correlation thread
        #    through here.
        return await self._send(token, "sendMessage", body, recipient)

    async def _send(
        self, token: str, endpoint: str, payload: dict, recipient: str,
    ) -> str:
        """Single chokepoint for Telegram Bot-API POSTs used by the
        review dispatch. Returns the message_id on success, a sentinel
        string on failure. Failures are logged but never raised —
        review-channel dispatch must not break the workflow run."""
        url = f"https://api.telegram.org/bot{token}/{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
            mid = (data.get("result") or {}).get("message_id")
            return str(mid) if mid is not None else f"tg-msg:{recipient}"
        except httpx.HTTPError as exc:
            log.warning("telegram_send_failed",
                        endpoint=endpoint, error=str(exc)[:200])
            return f"failed:{recipient}"

    @staticmethod
    def _first_media(message: ReviewMessage) -> dict | None:
        """Return the first {url, kind, alt_text} media entry across all
        drafts on this review. We render only the first asset in the
        bot DM — Telegram doesn't natively combine media + caption +
        buttons for multi-media albums in one message, and a single
        preview is enough for the reviewer to decide. The actual
        published post still gets all media from the draft."""
        for d in (message.drafts or []):
            for m in d.get("media") or []:
                if isinstance(m, dict) and m.get("url"):
                    return m
        return None

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

    # Telegram's sendMessage payload caps at 4096 chars. Leave a small
    # safety margin so headers, targets block, hashtags, and per-draft
    # framing fit cleanly without surprise truncation by Telegram itself.
    _TELEGRAM_TEXT_LIMIT = 4000
    # Minimum chars we'll show per draft even when there are many — below
    # this it's not useful preview, the reviewer should go to the web UI.
    _MIN_PER_DRAFT_CHARS = 250

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

        # Build the FULL untruncated draft preview first, then check
        # whether it fits. For the common single-target case the full
        # draft text gets shown (Instagram captions max 2200, LinkedIn
        # 3000 — both fit comfortably under the ~3700 char budget left
        # after headers). For multi-target, allocate proportionally
        # and only truncate if the total overflows.
        shown = drafts[:5]
        overhead = (
            len(f"*{header}*")
            + (len("*Targets:*\n" + targets_block) + 2 if targets_block else 0)
            + (len(f"\n\n_(+{len(drafts) - 5} more targets)_") if len(drafts) > 5 else 0)
            + (len(shown) * 6)   # rough markup-per-tile budget
        )
        budget = max(TelegramReviewChannel._TELEGRAM_TEXT_LIMIT - overhead, 500)
        # Per-draft budget — share remainder evenly. With 1 draft you
        # get the whole budget (typically 3700+); with 5 you get ~700
        # each (still enough for IG-length copy).
        per_target_chars = max(
            budget // max(1, len(shown)),
            TelegramReviewChannel._MIN_PER_DRAFT_CHARS,
        )

        preview_chunks = []
        for d in shown:
            tile_header = _md_escape(
                d.get("display_name")
                or d.get("plugin_name")
                or d.get("platform_name")
                or ""
            )
            if d.get("account_handle"):
                tile_header += f" · {_md_escape(str(d['account_handle']))}"
            raw_text = _md_escape(d.get("text") or "")
            if len(raw_text) <= per_target_chars:
                body = raw_text
            else:
                # Truncate at a word boundary so we don't cut a word in
                # half. Add a clear indicator and a hint to the web UI.
                cutoff = raw_text.rfind(" ", 0, per_target_chars - 40)
                if cutoff < per_target_chars // 2:
                    cutoff = per_target_chars - 40
                body = (
                    raw_text[:cutoff].rstrip()
                    + "\n\n_… draft truncated — open the Reviews page for the full text_"
                )
            # Surface hashtags + media count for the reviewer's context —
            # these are part of the post but live in separate fields.
            hashtags = d.get("hashtags") or []
            tile_footer = ""
            if hashtags:
                tile_footer += "\n" + _md_escape(" ".join(hashtags))
            media = d.get("media") or []
            if media:
                tile_footer += f"\n_📎 {len(media)} media attached_"
            preview_chunks.append(f"*{tile_header}*\n{body}{tile_footer}")
        preview = "\n\n".join(preview_chunks)
        if len(drafts) > 5:
            preview += f"\n\n_(+{len(drafts) - 5} more targets)_"

        text_parts = [f"*{header}*"]
        if targets_block:
            text_parts.append("*Targets:*\n" + targets_block)
        if preview:
            text_parts.append(preview)
        text = "\n\n".join(text_parts)[:TelegramReviewChannel._TELEGRAM_TEXT_LIMIT]
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
